"""Probe media with ffprobe into a typed, documented summary."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from cutroom.errors import MediaError
from cutroom.system.ffmpeg import run_ffprobe_json

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".mxf", ".wmv", ".flv", ".3gp"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aif", ".aiff", ".wma"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".heic"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS

_HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
_STANDARD_RATES = [23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 50.0, 59.94, 60.0, 100.0, 119.88, 120.0]


class VideoStream(BaseModel):
    index: int
    codec: str
    width: int                 # stored (coded) size
    height: int
    display_width: int         # after rotation and sample aspect ratio
    display_height: int
    rotation: int = 0
    fps: float | None          # nominal frame rate (r_frame_rate)
    avg_fps: float | None
    likely_vfr: bool = False
    pix_fmt: str | None = None
    bit_depth: int | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    is_hdr: bool = False
    start_time: float = 0.0
    duration: float | None = None
    frames: int | None = None


class AudioStream(BaseModel):
    index: int
    codec: str
    sample_rate: int
    channels: int
    layout: str | None = None
    start_time: float = 0.0
    duration: float | None = None
    language: str | None = None


class MediaInfo(BaseModel):
    path: str
    size_bytes: int
    kind: Literal["video", "audio", "image"]
    container: str
    duration: float
    start_time: float = 0.0
    bit_rate: int | None = None
    video: list[VideoStream] = []
    audio: list[AudioStream] = []
    subtitle_streams: int = 0
    creation_time: str | None = None
    timecode: str | None = None

    @property
    def primary_video(self) -> VideoStream | None:
        return self.video[0] if self.video else None

    @property
    def primary_audio(self) -> AudioStream | None:
        return self.audio[0] if self.audio else None

    @property
    def aspect_label(self) -> str:
        v = self.primary_video
        if not v:
            return "audio"
        r = v.display_width / max(1, v.display_height)
        if abs(r - 16 / 9) < 0.02:
            return "16:9"
        if abs(r - 9 / 16) < 0.02:
            return "9:16"
        if abs(r - 1) < 0.02:
            return "1:1"
        if abs(r - 4 / 3) < 0.02:
            return "4:3"
        if abs(r - 4 / 5) < 0.02:
            return "4:5"
        return f"{r:.3f}:1"

    def summary(self) -> str:
        v, a = self.primary_video, self.primary_audio
        parts = [f"{self.kind}", f"{self.duration:.2f}s"]
        if v:
            parts.append(f"{v.display_width}x{v.display_height} ({self.aspect_label})")
            if v.fps:
                parts.append(f"{v.fps:.3f} fps" + (" VFR?" if v.likely_vfr else ""))
            parts.append(v.codec + (" HDR" if v.is_hdr else ""))
        if a:
            parts.append(f"audio {a.codec} {a.sample_rate} Hz {a.channels}ch")
        if len(self.audio) > 1:
            parts.append(f"{len(self.audio)} audio streams")
        return ", ".join(parts)


def _rate(value: str | None) -> float | None:
    if not value or value in ("0/0", "0"):
        return None
    try:
        f = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None
    return f if f > 0 else None


def snap_fps(fps: float | None) -> float | None:
    """Snap a measured rate to the nearest broadcast standard (29.97003 -> 29.97)."""
    if fps is None:
        return None
    best = min(_STANDARD_RATES, key=lambda r: abs(r - fps))
    return best if abs(best - fps) / fps < 0.005 else round(fps, 3)


_NTSC = {23.976: "24000/1001", 29.97: "30000/1001", 47.952: "48000/1001", 59.94: "60000/1001",
         119.88: "120000/1001"}


def fps_rational(fps: float) -> str:
    """Exact rate for FFmpeg: 29.97 -> '30000/1001', 25.0 -> '25'."""
    if fps in _NTSC:
        return _NTSC[fps]
    return str(int(fps)) if float(fps).is_integer() else f"{fps:.6f}"


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rotation(stream: dict) -> int:
    rot = 0
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        rot = int(float(tags["rotate"]))
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    return rot % 360


def _video_stream(s: dict, fmt_start: float) -> VideoStream:
    w, h = int(s.get("width") or 0), int(s.get("height") or 0)
    rot = _rotation(s)
    sar = _rate(s.get("sample_aspect_ratio")) or 1.0
    dw, dh = int(round(w * sar)), h
    if rot in (90, 270):
        dw, dh = dh, dw
    fps = _rate(s.get("r_frame_rate"))
    avg = _rate(s.get("avg_frame_rate"))
    likely_vfr = bool(fps and avg and abs(fps - avg) / fps > 0.01)
    pix = s.get("pix_fmt")
    depth = int(s["bits_per_raw_sample"]) if str(s.get("bits_per_raw_sample", "")).isdigit() else (
        10 if pix and ("10" in pix) else (12 if pix and "12" in pix else 8 if pix else None))
    transfer = s.get("color_transfer")
    nb = s.get("nb_frames")
    return VideoStream(
        index=s["index"], codec=s.get("codec_name", "unknown"), width=w, height=h,
        display_width=dw, display_height=dh, rotation=rot,
        fps=snap_fps(fps), avg_fps=round(avg, 3) if avg else None, likely_vfr=likely_vfr,
        pix_fmt=pix, bit_depth=depth, color_transfer=transfer,
        color_primaries=s.get("color_primaries"), is_hdr=transfer in _HDR_TRANSFERS,
        start_time=round((_float(s.get("start_time")) or fmt_start) - fmt_start, 6),
        duration=_float(s.get("duration")),
        frames=int(nb) if str(nb or "").isdigit() else None,
    )


def _audio_stream(s: dict, fmt_start: float) -> AudioStream:
    return AudioStream(
        index=s["index"], codec=s.get("codec_name", "unknown"),
        sample_rate=int(s.get("sample_rate") or 0), channels=int(s.get("channels") or 0),
        layout=s.get("channel_layout"),
        start_time=round((_float(s.get("start_time")) or fmt_start) - fmt_start, 6),
        duration=_float(s.get("duration")),
        language=(s.get("tags") or {}).get("language"),
    )


def probe(path: str | Path) -> MediaInfo:
    """Inspect ``path``. Raises :class:`MediaError` if it is not usable media."""
    path = Path(path)
    if not path.is_file():
        raise MediaError(f"File not found: {path}", fix="Check the path; quote paths that contain spaces.")
    data = json.loads(run_ffprobe_json(path, ["-show_format", "-show_streams"]))
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    fmt_start = _float(fmt.get("start_time")) or 0.0
    attached_pic = lambda s: (s.get("disposition") or {}).get("attached_pic") == 1  # noqa: E731
    vids = [_video_stream(s, fmt_start) for s in streams
            if s.get("codec_type") == "video" and not attached_pic(s)]
    auds = [_audio_stream(s, fmt_start) for s in streams if s.get("codec_type") == "audio"]
    subs = sum(1 for s in streams if s.get("codec_type") == "subtitle")
    if not vids and not auds:
        raise MediaError(f"{path.name} has no audio or video streams.",
                         fix="Is this really a media file? Try opening it in a video player.")
    duration = _float(fmt.get("duration"))
    is_image = bool(vids) and (path.suffix.lower() in IMAGE_EXTS or (duration or 0) == 0 and not auds)
    kind = "image" if is_image else ("video" if vids else "audio")
    if kind != "image" and not duration:
        duration = max([s.duration or 0 for s in [*vids, *auds]] or [0])
        if not duration:
            raise MediaError(f"Could not determine the duration of {path.name}.",
                             fix="The file may be truncated. Re-export or re-copy it.")
    tags = fmt.get("tags") or {}
    timecode = tags.get("timecode") or next(
        ((s.get("tags") or {}).get("timecode") for s in streams if (s.get("tags") or {}).get("timecode")), None)
    return MediaInfo(
        path=str(path.resolve()), size_bytes=int(fmt.get("size") or path.stat().st_size),
        kind=kind, container=fmt.get("format_name", "unknown"), duration=round(duration or 0.0, 6),
        start_time=round(fmt_start, 6),
        bit_rate=int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else None,
        video=vids, audio=auds, subtitle_streams=subs,
        creation_time=tags.get("creation_time"), timecode=timecode,
    )
