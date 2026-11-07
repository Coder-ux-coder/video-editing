"""Locate FFmpeg, inspect what it can do, and run it with loud, useful errors."""

from __future__ import annotations

import functools
import os
import platform
import re
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from cutroom.errors import CutroomError, ToolMissingError
from cutroom.paths import tools_dir

# Filters the pipeline depends on. Missing any of these is a hard failure.
REQUIRED_FILTERS = {
    "loudnorm": "EBU R128 loudness normalisation",
    "ebur128": "loudness measurement",
    "silencedetect": "silence detection",
    "astats": "audio level statistics",
    "afftdn": "FFT noise reduction",
    "highpass": "voice EQ",
    "equalizer": "voice EQ",
    "acompressor": "voice compression",
    "alimiter": "true-peak limiting",
    "sidechaincompress": "music ducking",
    "acrossfade": "click-free audio joins",
    "afade": "click-free audio joins",
    "scale": "proxies and resizing",
    "crop": "reframing",
    "overlay": "compositing",
    "fps": "constant frame-rate conforming",
    "tile": "contact sheets",
    "drawtext": "burned timecode and labels",
    "subtitles": "caption burn-in (libass)",
    "blackdetect": "QA: black frames",
    "freezedetect": "QA: frozen frames",
    "scdet": "scene-change detection",
}

# Nice to have. Missing ones are warnings with a reduced feature set.
OPTIONAL_FILTERS = {
    "arnndn": "neural speech denoise",
    "deesser": "de-essing",
    "zscale": "HDR to SDR tone mapping",
    "tonemap": "HDR to SDR tone mapping",
    "vidstabdetect": "stabilisation",
    "lut3d": "LUT colour grading",
    "rubberband": "pitch-preserving speed changes",
    "signalstats": "exposure/colour QA",
    "blurdetect": "QA: blur",
}

REQUIRED_ENCODERS = {"libx264": "H.264 delivery", "aac": "AAC audio", "pcm_s16le": "WAV audio", "flac": "lossless audio"}
OPTIONAL_ENCODERS = {"libx265": "HEVC", "png": "frame grabs", "mjpeg": "JPEG frame grabs"}

# Hardware H.264 encoders, in preference order, with the extra args each needs.
HW_ENCODER_CANDIDATES: dict[str, list[str]] = {
    "h264_nvenc": [],
    "h264_videotoolbox": [],
    "h264_qsv": [],
    "h264_amf": [],
    "h264_vaapi": ["-vaapi_device", "/dev/dri/renderD128", "-vf", "format=nv12,hwupload"],
    "h264_mf": [],
}

MIN_FFMPEG_MAJOR = 6


def _exe_name(name: str) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("CUTROOM_FFMPEG_DIR")
    if env:
        dirs.append(Path(env))
    # Portable build that setup may download (no admin rights needed).
    local = tools_dir() / "ffmpeg"
    dirs += [local / "bin", local]
    if platform.system() == "Windows":
        # winget installs Gyan.FFmpeg here and PATH only updates in new shells.
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        if base.is_dir():
            dirs += sorted(base.glob("Gyan.FFmpeg*/ffmpeg-*/bin"), reverse=True)
            dirs += sorted(base.glob("BtbN.FFmpeg*/ffmpeg-*/bin"), reverse=True)
    if platform.system() == "Darwin":
        dirs += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    return dirs


@functools.lru_cache(maxsize=None)
def find_tool(name: str) -> Path | None:
    """Find ``ffmpeg`` or ``ffprobe``. Explicit locations win over PATH."""
    for d in _candidate_dirs():
        p = d / _exe_name(name)
        if p.is_file():
            return p
    found = shutil.which(name)
    return Path(found) if found else None


def require_tool(name: str) -> Path:
    path = find_tool(name)
    if path is None:
        raise ToolMissingError(
            f"{name} was not found.",
            fix="Run the setup script (./setup.sh on macOS/Linux, .\\setup.ps1 on Windows), "
            "or install FFmpeg and make sure it is on PATH, or set CUTROOM_FFMPEG_DIR.",
        )
    return path


@dataclass
class FFmpegInfo:
    ffmpeg: Path
    ffprobe: Path
    version: str
    major: int | None
    configuration: str
    filters: set[str] = field(default_factory=set)
    encoders: set[str] = field(default_factory=set)
    decoders: set[str] = field(default_factory=set)

    @property
    def missing_required_filters(self) -> list[str]:
        return sorted(f for f in REQUIRED_FILTERS if f not in self.filters)

    @property
    def missing_optional_filters(self) -> list[str]:
        return sorted(f for f in OPTIONAL_FILTERS if f not in self.filters)

    @property
    def missing_required_encoders(self) -> list[str]:
        return sorted(e for e in REQUIRED_ENCODERS if e not in self.encoders)

    def to_dict(self) -> dict:
        return {
            "ffmpeg": str(self.ffmpeg),
            "ffprobe": str(self.ffprobe),
            "version": self.version,
            "major": self.major,
            "missing_required_filters": self.missing_required_filters,
            "missing_optional_filters": self.missing_optional_filters,
            "missing_required_encoders": self.missing_required_encoders,
        }


def _capture(cmd: Sequence[str | os.PathLike], timeout: float = 60) -> str:
    out = subprocess.run(
        [str(c) for c in cmd], capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    return out.stdout + out.stderr


def _parse_names(listing: str) -> set[str]:
    """Parse the name column of ``-filters`` / ``-encoders`` / ``-decoders`` output."""
    names = set()
    for line in listing.splitlines():
        m = re.match(r"^\s*[A-Z.|]{3,6}\s+([A-Za-z0-9_\-]+)\s", line)
        if m and m.group(1) not in {"=", "Filters", "Encoders", "Decoders"}:
            names.add(m.group(1))
    return names


@functools.lru_cache(maxsize=None)
def ffmpeg_info() -> FFmpegInfo:
    ffmpeg = require_tool("ffmpeg")
    ffprobe = require_tool("ffprobe")
    head = _capture([ffmpeg, "-hide_banner", "-version"])
    m = re.search(r"ffmpeg version (\S+)", head)
    version = m.group(1) if m else "unknown"
    mm = re.match(r"n?(\d+)\.", version)
    major = int(mm.group(1)) if mm else None
    cfg = re.search(r"configuration: (.*)", head)
    return FFmpegInfo(
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        version=version,
        major=major,
        configuration=cfg.group(1) if cfg else "",
        filters=_parse_names(_capture([ffmpeg, "-hide_banner", "-filters"])),
        encoders=_parse_names(_capture([ffmpeg, "-hide_banner", "-encoders"])),
        decoders=_parse_names(_capture([ffmpeg, "-hide_banner", "-decoders"])),
    )


def test_encoder(name: str, extra: Sequence[str] = ()) -> bool:
    """Compiled-in is not the same as usable: actually encode a few frames."""
    ffmpeg = require_tool("ffmpeg")
    extra = list(extra)
    vf: list[str] = []
    if "-vf" in extra:
        i = extra.index("-vf")
        vf = ["-vf", extra[i + 1]]
        extra = extra[:i] + extra[i + 2:]
    cmd = [str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", *extra,
           "-f", "lavfi", "-i", "color=c=black:s=256x256:r=30:d=0.2",
           *vf, "-c:v", name, "-frames:v", "5", "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0


def working_hw_encoders() -> list[str]:
    info = ffmpeg_info()
    return [name for name, extra in HW_ENCODER_CANDIDATES.items()
            if name in info.encoders and test_encoder(name, extra)]


# --------------------------------------------------------------------------- running

_FIX_HINTS = [
    (r"No such file or directory", "Check the input path exists and is spelled correctly."),
    (r"Invalid data found when processing input|moov atom not found",
     "The file looks corrupt or incomplete (an interrupted copy or recording). Re-copy it from the camera/card."),
    (r"Unknown encoder|Encoder not found", "This FFmpeg build lacks an encoder. Run `cutroom doctor` for details."),
    (r"No space left on device", "The disk is full. Free space or set CUTROOM_JOBS_DIR to a bigger drive."),
    (r"Permission denied", "Cutroom cannot read/write that location. Check file permissions."),
    (r"No such filter", "This FFmpeg build lacks a filter. Run `cutroom doctor` and reinstall FFmpeg via setup."),
    (r"Cannot allocate memory|Out of memory", "Out of memory. Close other apps or lower render concurrency in config/machine.json."),
]


def _suggest_fix(stderr: str) -> str:
    for pattern, hint in _FIX_HINTS:
        if re.search(pattern, stderr, re.IGNORECASE):
            return hint
    return "Re-run with --verbose to see the full FFmpeg log."


def run_ffmpeg(
    args: Sequence[str | os.PathLike],
    *,
    desc: str = "ffmpeg",
    duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    timeout: float | None = None,
) -> str:
    """Run ffmpeg with ``args`` (everything after the binary name).

    - No shell: arguments are passed as a list, so paths with spaces are safe.
    - ``on_progress`` receives a 0..1 fraction when ``duration`` is known.
    - Raises :class:`CutroomError` with the stderr tail and a suggested fix.
    Returns stderr (useful for analysis filters that print to the log).
    """
    ffmpeg = require_tool("ffmpeg")
    cmd = [str(ffmpeg), "-hide_banner", "-nostdin", "-progress", "pipe:1", "-nostats",
           *[str(a) for a in args]]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    stderr_lines: deque[str] = deque(maxlen=4000)

    def drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    assert proc.stdout is not None
    for line in proc.stdout:
        if on_progress and duration and line.startswith("out_time_us="):
            try:
                us = int(line.split("=", 1)[1])
                on_progress(max(0.0, min(1.0, us / 1e6 / duration)))
            except ValueError:
                pass
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise CutroomError(f"{desc}: FFmpeg timed out after {timeout}s.",
                           fix="The input may be very long or the machine overloaded; retry or raise the timeout.")
    t.join(timeout=5)
    stderr = "".join(stderr_lines)
    if proc.returncode != 0:
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        raise CutroomError(f"{desc} failed (FFmpeg exit code {proc.returncode}).",
                           fix=_suggest_fix(stderr), details=tail)
    if on_progress:
        on_progress(1.0)
    return stderr


def run_ffprobe_json(path: str | os.PathLike, extra: Sequence[str] = ()) -> str:
    ffprobe = require_tool("ffprobe")
    cmd = [str(ffprobe), "-v", "error", "-print_format", "json", *extra, str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        from cutroom.errors import MediaError
        raise MediaError(f"ffprobe could not read {path}.", fix=_suggest_fix(r.stderr),
                         details=r.stderr.strip()[-2000:])
    return r.stdout
