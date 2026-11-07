"""Low-resolution proxies and extracted audio, for fast analysis and previews.

Proxies are constant frame rate with a 1-second GOP so frame grabs and seeks
are fast and exact. Only final renders read the full-resolution sources.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from cutroom.ingest.probe import MediaInfo, fps_rational, probe
from cutroom.job import Job, SourceRecord
from cutroom.paths import rel_or_abs
from cutroom.system.ffmpeg import ffmpeg_info, run_ffmpeg

PROXY_VERSION = 1   # bump when the proxy recipe changes so stale proxies are rebuilt
AUDIO_VERSION = 1


def proxy_size(display_w: int, display_h: int, short_side: int) -> tuple[int, int]:
    """Scale so the short side is ``short_side`` (never upscale), both sides even."""
    short = min(display_w, display_h)
    target = min(short_side, short)
    scale = target / short
    w = max(2, int(round(display_w * scale / 2)) * 2)
    h = max(2, int(round(display_h * scale / 2)) * 2)
    return w, h


def _params_key(params: dict) -> str:
    return hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]


def _tonemap_chain() -> str:
    return ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")


def build_proxy_args(src: Path, dst: Path, info: MediaInfo, *, short_side: int) -> list[str]:
    v = info.primary_video
    assert v is not None
    w, h = proxy_size(v.display_width, v.display_height, short_side)
    fps = v.fps or 30.0
    gop = max(1, int(round(fps)))
    filters = []
    caps = ffmpeg_info().filters
    if v.is_hdr and {"zscale", "tonemap"} <= caps:
        filters.append(_tonemap_chain())
    filters += [f"scale={w}:{h}:flags=bicubic", "setsar=1", f"fps={fps_rational(fps)}", "format=yuv420p"]
    args = ["-y", "-i", str(src), "-map", f"0:{v.index}"]
    a = info.primary_audio
    if a:
        args += ["-map", f"0:{a.index}"]
    args += ["-vf", ",".join(filters),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
             "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    if a:
        args += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", str(min(2, max(1, a.channels)))]
    args += ["-movflags", "+faststart", "-map_metadata", "-1", str(dst)]
    return args


def make_proxy(job: Job, src: SourceRecord, *, short_side: int = 540, force: bool = False,
               on_progress: Callable[[float], None] | None = None) -> dict:
    """Create ``proxy/<sid>.mp4``. Idempotent: skipped when inputs and recipe are unchanged."""
    info = src.media
    if info.kind != "video":
        return {"source": src.id, "status": "skipped", "reason": f"{info.kind} source has no video"}
    params = {"v": PROXY_VERSION, "short_side": short_side, "hash": src.hash}
    dst = job.root / "proxy" / f"{src.id}.mp4"
    if not force and dst.is_file() and src.proxy_params == params:
        return {"source": src.id, "status": "up-to-date", "path": rel_or_abs(dst, job.root)}
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".partial.mp4")
    run_ffmpeg(build_proxy_args(job.source_path(src), tmp, info, short_side=short_side),
               desc=f"proxy {src.id}", duration=info.duration, on_progress=on_progress)
    tmp.replace(dst)
    out = probe(dst)
    src.proxy = rel_or_abs(dst, job.root)
    src.proxy_params = params
    job.save()
    pv = out.primary_video
    drift = abs(out.duration - info.duration)
    job.log.note(f"{src.id} proxy → `{src.proxy}` {pv.display_width}x{pv.display_height} @ {pv.fps} fps CFR "
                 f"(duration drift {drift * 1000:.0f} ms).")
    if drift > 0.25:
        job.log.warn(f"{src.id} proxy duration differs from source by {drift:.2f}s; check for VFR/timestamp issues.")
    return {"source": src.id, "status": "created", "path": src.proxy,
            "size": f"{pv.display_width}x{pv.display_height}", "fps": pv.fps, "duration_drift_s": round(drift, 3)}


def extract_audio(job: Job, src: SourceRecord, *, force: bool = False,
                  on_progress: Callable[[float], None] | None = None) -> dict:
    """Extract the primary audio stream twice.

    - ``asr``: 16 kHz mono 16-bit WAV, the exact samples the speech model hears.
    - ``analysis``: 48 kHz FLAC (lossless, up to stereo) for loudness/noise/silence analysis.
    """
    a = src.media.primary_audio
    if a is None:
        return {"source": src.id, "status": "skipped", "reason": "no audio stream"}
    out_dir = job.analysis_dir(src.id)
    targets = {
        "asr": (out_dir / "audio_16k.wav", ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]),
        "analysis": (out_dir / "audio_48k.flac",
                     ["-ac", str(min(2, max(1, a.channels))), "-ar", "48000", "-c:a", "flac", "-sample_fmt", "s16"]),
    }
    params_ok = src.audio.get("_params") == _params_key({"v": AUDIO_VERSION, "hash": src.hash})
    if not force and params_ok and all(p.is_file() for p, _ in targets.values()):
        return {"source": src.id, "status": "up-to-date", **{k: rel_or_abs(p, job.root) for k, (p, _) in targets.items()}}
    srcp = job.source_path(src)
    # One decode, two outputs.
    args = ["-y", "-i", str(srcp)]
    tmp_paths = []
    for _, (p, codec_args) in targets.items():
        tmp = p.with_name(p.stem + ".partial" + p.suffix)
        tmp_paths.append((tmp, p))
        args += ["-map", f"0:{a.index}", "-vn", *codec_args, str(tmp)]
    run_ffmpeg(args, desc=f"extract audio {src.id}", duration=src.media.duration, on_progress=on_progress)
    for tmp, final in tmp_paths:
        tmp.replace(final)
    src.audio = {k: rel_or_abs(p, job.root) for k, (p, _) in targets.items()}
    src.audio["_params"] = _params_key({"v": AUDIO_VERSION, "hash": src.hash})
    job.save()
    job.log.note(f"{src.id} audio → 16 kHz mono WAV (ASR) + 48 kHz FLAC (analysis); "
                 f"stream offset {a.start_time * 1000:.0f} ms from container start.")
    return {"source": src.id, "status": "created", **{k: v for k, v in src.audio.items() if not k.startswith("_")}}
