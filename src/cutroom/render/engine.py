"""Render a timeline to video.

Video: clips are grouped into *runs* (consecutive clips from one source in increasing source
order). Each run is one FFmpeg pass that conforms the source to the output frame rate and
keeps exact frame-number ranges (``select=between(n,a,b)``): one decode, no buffering, no drift.
Runs are concatenated without re-encoding.

Audio: assembled sample-accurately in Python from the 48 kHz analysis audio, with a short
raised-cosine fade on both sides of every join, so cuts never click. Clip edges are snapped
to the output frame grid first, so audio and video lengths match exactly.
"""

from __future__ import annotations

import functools
import json
import sys
import math
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

import numpy as np
import soundfile as sf

from cutroom.errors import CutroomError, MediaError
from cutroom.ingest.probe import fps_rational, probe
from cutroom.job import Job, atomic_write_text
from cutroom.system.ffmpeg import ffmpeg_info, require_tool, run_ffmpeg
from cutroom.timeline.model import Timeline

SR = 48000
AAC_PRIMING = 1024   # samples FFmpeg's AAC encoder prepends (its initial_padding)
Mode = Literal["preview", "final"]


@dataclass
class Segment:
    clip_id: str
    source: str
    n_in: int        # first output-rate frame (source time = n / fps)
    n_out: int       # exclusive
    out_frame: int   # position on the output timeline (frames)
    zoom: tuple | None = None   # (scale0, x0, y0, scale1, x1, y1): punch-in/push, crop centre in frame fractions
    vframe: tuple | None = None  # vertical output: ("fill",) or ("crop", [(frame offset, centre x), ...])

    @property
    def frames(self) -> int:
        return self.n_out - self.n_in


def plan_segments(tl: Timeline) -> list[Segment]:
    """Snap every V1 clip to the output frame grid. Speed changes are not supported yet."""
    fps = tl.meta.fps
    segs, pos = [], 0
    for c in tl.track_clips("V1"):
        if abs(c.speed - 1.0) > 1e-6:
            raise CutroomError(f"Clip {c.id} has speed {c.speed}; speed changes render in a later phase.",
                               fix="Set the clip speed back to 1.0.")
        n_in, n_out = int(round(c.src_in * fps)), int(round(c.src_out * fps))
        if n_out <= n_in:
            continue
        vf = _vframe(tl, c, fps, n_out - n_in)
        segs.append(Segment(c.id, c.source, n_in, n_out, pos, None if vf else _zoom(c), vf))
        pos += n_out - n_in
    return segs


def render_beds(job: Job, tl: Timeline, rng: tuple[float, float]) -> list[dict]:
    """Music beds and effects overlapping the rendered range, in render time, with file paths."""
    from cutroom.assets.library import asset_path, get_asset

    out = []
    for b in [*tl.audio.music, *tl.audio.sfx]:
        if b.end <= rng[0] or b.start >= rng[1]:
            continue
        out.append({"kind": b.kind, "path": str(asset_path(get_asset(b.asset))), "start": b.start - rng[0],
                    "end": min(b.end, rng[1]) - rng[0], "src_in": b.src_in + max(0.0, rng[0] - b.start),
                    "gain_db": b.gain_db, "fade_in": b.fade_in if b.start >= rng[0] else 0.0,
                    "fade_out": b.fade_out if b.end <= rng[1] else 0.0, "duck": b.duck})
    return out


def speech_spans(job: Job, tl: Timeline, rng: tuple[float, float]) -> list[tuple[float, float]]:
    """Every kept word in render time (fillers included: they are speech too), for ducking."""
    from cutroom.captions.plan import timeline_words
    from cutroom.timeline.ops import words_for

    words = {s.id: words_for(job, s.id) for s in tl.sources}
    return [(w.start - rng[0], w.end - rng[0]) for w in timeline_words(tl, words, set())
            if w.end > rng[0] and w.start < rng[1]]


def is_vertical(tl: Timeline, source_id: str) -> bool:
    """A portrait output cut from landscape footage (a Short from 16:9 material)."""
    s = tl.source(source_id)
    return tl.meta.height > tl.meta.width and bool(s.width and s.height and s.width > s.height)


def _vframe(tl: Timeline, c, fps: float, frames: int) -> tuple | None:
    if not is_vertical(tl, c.source):
        return None
    if any(e.name == "vertical_fill" for e in c.effects):
        return ("fill",)
    keys = [(min(frames - 1, int(round(k.t * fps))), k.x) for k in c.reframe] or [(0, 0.5)]
    return ("crop", keys)

def _zoom(c) -> tuple | None:
    """A clip's reframe as a constant punch-in or a linear push from its first to its last keyframe."""
    if not c.reframe:
        return None
    k0, k1 = c.reframe[0], c.reframe[-1]
    if max(k0.scale, k1.scale) <= 1.0001:
        return None
    return (k0.scale, k0.x, k0.y, k1.scale, k1.x, k1.y)


def clip_segments(segs: list[Segment], f0: int, f1: int) -> list[Segment]:
    """Keep only output frames [f0, f1) and renumber them from 0 (for range renders)."""
    out = []
    for s in segs:
        a, b = max(s.out_frame, f0), min(s.out_frame + s.frames, f1)
        if b <= a:
            continue
        n_in = s.n_in + (a - s.out_frame)
        vf = s.vframe
        if vf and vf[0] == "crop":     # keyframes are relative to the segment start: shift them
            off = a - s.out_frame
            keys = [(k - off, x) for k, x in vf[1]]
            before = [kx for kx in keys if kx[0] <= 0]
            keys = ([(0, before[-1][1])] if before else []) + [kx for kx in keys if kx[0] > 0]
            vf = ("crop", keys or [(0, vf[1][-1][1])])
        out.append(Segment(s.clip_id, s.source, n_in, n_in + (b - a), a - f0, s.zoom, vf))
    return out


def shift_captions(caps, t0: float, t1: float):
    """Captions re-timed for a range render that starts at timeline time t0."""
    c2 = caps.model_copy(deep=True)
    cues = []
    for c in c2.cues:
        if c.end <= t0 or c.start >= t1:
            continue
        c.start, c.end = max(0.0, c.start - t0), min(t1, c.end) - t0
        for w in c.words:
            w["start"], w["end"] = max(0.0, w["start"] - t0), max(0.0, w["end"] - t0)
        cues.append(c)
    c2.cues = cues
    return c2


def group_runs(segs: list[Segment]) -> list[list[Segment]]:
    runs: list[list[Segment]] = []
    for s in segs:
        same_layout = (runs[-1][-1].vframe or ("",))[0] == (s.vframe or ("",))[0] if runs else False
        if runs and runs[-1][-1].source == s.source and s.n_in >= runs[-1][-1].n_out and same_layout:
            runs[-1].append(s)
        else:
            runs.append([s])
    return runs


# --------------------------------------------------------------------------- audio


def raised_cosine(n: int) -> np.ndarray:
    return (0.5 - 0.5 * np.cos(np.linspace(0, math.pi, n, dtype=np.float32))) if n > 0 else np.ones(0, np.float32)


def render_audio(job: Job, tl: Timeline, segs: list[Segment], out_wav: Path, *, join_fade: float = 0.008) -> list[int]:
    """Write the edit's audio (48 kHz, float) and return the sample index of every join."""
    fps = tl.meta.fps
    fade = int(join_fade * SR)
    joins: list[int] = []
    channels = 2
    with sf.SoundFile(str(out_wav), "w", samplerate=SR, channels=channels, subtype="FLOAT") as outf:
        written = 0
        for k, s in enumerate(segs):
            src = job.source(s.source)
            if not src.audio.get("analysis"):
                raise MediaError(f"Source {s.source} has no extracted audio.", fix=f"Run `cutroom proxy {job.id}`.")
            offset = src.media.primary_audio.start_time if src.media.primary_audio else 0.0
            a = int(round((s.n_in / fps - offset) * SR))
            out_start = int(round(s.out_frame * SR / fps))
            out_end = int(round((s.out_frame + s.frames) * SR / fps))
            n = out_end - out_start
            with sf.SoundFile(str(job.abspath(src.audio["analysis"]))) as f:
                pad_front = max(0, -a)
                f.seek(max(0, a))
                x = f.read(n - pad_front, dtype="float32", always_2d=True)
            if x.shape[1] == 1:
                x = np.repeat(x, 2, axis=1)
            x = x[:, :2]
            if pad_front or len(x) < n:
                x = np.concatenate([np.zeros((pad_front, 2), np.float32), x,
                                    np.zeros((max(0, n - pad_front - len(x)), 2), np.float32)])
            prev = segs[k - 1] if k else None
            nxt = segs[k + 1] if k + 1 < len(segs) else None
            contiguous_before = prev is not None and prev.source == s.source and prev.n_out == s.n_in
            contiguous_after = nxt is not None and nxt.source == s.source and nxt.n_in == s.n_out
            f_len = min(fade, n // 2)
            if not contiguous_before:
                x[:f_len] *= raised_cosine(f_len)[:, None]
                if k:
                    joins.append(written)
            if not contiguous_after:
                x[n - f_len:] *= raised_cosine(f_len)[::-1, None]
            outf.write(x)
            written += n
    return joins


def detect_clicks(wav: Path, joins: list[int], *, ratio: float = 5.0, min_jump: float = 0.02) -> list[dict]:
    """A join clicks if it breaks the waveform's local trend far more than the audio around it does.

    For each sample the next value is predicted by linear extrapolation of the previous two;
    a cut discontinuity gives a large prediction error exactly at the join. The join's error
    is compared with the RMS prediction error over the surrounding ±50 ms (excluding ±2 ms).
    """
    clicks = []
    with sf.SoundFile(str(wav)) as f:
        sr = f.samplerate
        half, near = int(0.05 * sr), int(0.002 * sr)
        for j in joins:
            start = max(0, j - half)
            f.seek(start)
            x = f.read(2 * half, dtype="float32", always_2d=True).mean(axis=1).astype(np.float64)
            if len(x) < 4 * near:
                continue
            err = np.abs(x[2:] - (2 * x[1:-1] - x[:-2]))   # err[i] is the error predicting x[i + 2]
            c = j - start - 2
            local = float(err[max(0, c - 2): c + 3].max())
            around = np.concatenate([err[: max(0, c - near)], err[c + near:]])
            ref = float(np.sqrt(np.mean(around ** 2))) if len(around) else 0.0
            if local > min_jump and local > ratio * max(ref, 1e-4):
                clicks.append({"sample": j, "time": round(j / sr, 3), "jump": round(local, 4),
                               "ratio": round(local / max(ref, 1e-4), 1)})
    return clicks


def has_edit_list(mp4: Path) -> bool:
    """True if any track of an MP4 has a non-trivial edit list (YouTube asks for none)."""
    import struct

    with open(mp4, "rb") as f:
        data = f.read(64 * 1024 * 1024)

    def walk(off: int, end: int) -> bool:
        while off + 8 <= end:
            size, typ = struct.unpack(">I4s", data[off:off + 8])
            hdr = 8
            if size == 1:
                size, hdr = struct.unpack(">Q", data[off + 8:off + 16])[0], 16
            if size < 8:
                return False
            if typ == b"elst":
                return True
            if typ in (b"moov", b"trak", b"edts") and walk(off + hdr, min(end, off + size)):
                return True
            off += size
        return False

    return walk(0, len(data))


# --------------------------------------------------------------------------- video


@functools.lru_cache(maxsize=1)
def drawtext_works() -> bool:
    ffmpeg = require_tool("ffmpeg")
    r = subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=black:s=64x64:d=0.1", "-vf", "drawtext=text=x", "-f", "null", "-"],
                       capture_output=True)
    return r.returncode == 0


def _encoder_args(mode: Mode, encoder: str) -> list[str]:
    if mode == "final":
        return ["-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high", "-pix_fmt", "yuv420p"]
    if encoder != "libx264":
        return ["-c:v", encoder, "-b:v", "3M", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]


def output_size(tl: Timeline, mode: Mode, preview_short: int = 540) -> tuple[int, int]:
    w, h = tl.meta.width, tl.meta.height
    if mode == "preview":
        scale = preview_short / min(w, h)
        w, h = int(round(w * scale / 2)) * 2, int(round(h * scale / 2)) * 2
    return w, h


def render_run(job: Job, tl: Timeline, run: list[Segment], out_path: Path, *, mode: Mode, encoder: str,
               timecode: bool, scratch: Path, on_progress: Callable[[float], None] | None = None,
               overlay: str | None = None, broll: list[dict] | None = None) -> None:
    src = job.source(run[0].source)
    fps = tl.meta.fps
    use_proxy = mode == "preview" and src.proxy and job.abspath(src.proxy).is_file()
    inp = job.abspath(src.proxy) if use_proxy else job.source_path(src)
    w, h = output_size(tl, mode)
    sel = "+".join(f"between(n\\,{s.n_in}\\,{s.n_out - 1})" for s in run)
    chain = [f"fps={fps_rational(fps)}", f"select='{sel}'", f"setpts=N/({fps_rational(fps)})/TB"]
    zoom = zoom_filter(run, fps, w, h)
    sw, sh = src.media.primary_video.display_width, src.media.primary_video.display_height
    same_aspect = abs(sw / sh - w / h) < 0.01
    vf = run[0].vframe
    if vf and vf[0] == "fill":
        style = _style_value(tl, ("shorts", "fill_blur"), 28)
        sigma = max(4, int(round(style * w / 1080)))
        graph = ("[0:v]" + ",".join(chain) + ",split=2[fa][fb];"
                 f"[fa]scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic,crop={w}:{h},"
                 f"gblur=sigma={sigma},eq=brightness=-0.08:saturation=0.85[fbg];"
                 f"[fb]scale={w}:-2:flags=lanczos[ffg];[fbg][ffg]overlay=0:(H-h)/2,setsar=1")
    else:
        if vf and vf[0] == "crop":
            chain += [vertical_crop_filter(run, w, h), f"scale={w}:{h}:flags=lanczos", "setsar=1"]
        elif zoom and same_aspect:
            chain += [zoom, "setsar=1"]          # crop from the full-resolution frame, scale once
        else:
            chain += [f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bicubic",
                      f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2", "setsar=1"]
            if zoom:
                chain += [zoom, "setsar=1"]
        graph = "[0:v]" + ",".join(chain)
    chain = []
    extra_inputs: list[str] = []
    r0 = run[0].out_frame
    r1 = r0 + sum(s.frames for s in run)
    k = 1
    for b in broll or []:
        # A cutaway pre-rendered to exact frames, placed by timestamp inside this run; it covers
        # the main picture only (main audio continues) and sits under captions and graphics.
        a, z = max(b["f0"], r0), min(b["f0"] + b["frames"], r1)
        if z <= a:
            continue
        extra_inputs += ["-i", b["path"]]
        skip, at = a - b["f0"], a - r0
        graph += (f"[pre{k}];[{k}:v]trim=start_frame={skip}:end_frame={skip + z - a},setpts=PTS-STARTPTS+{at}/({fps_rational(fps)})/TB,"
                  f"format=yuv420p,setsar=1[br{k}];[pre{k}][br{k}]overlay=0:0:eof_action=pass")
        k += 1
    if overlay:
        # Transparent caption/graphics frames numbered in output-timeline frames: this run reads
        # exactly its own frames, starting at its first output frame.
        extra_inputs += ["-framerate", fps_rational(fps), "-start_number", str(run[0].out_frame), "-i", overlay]
        graph += f"[base];[{k}:v]format=rgba[ov];[base][ov]overlay=0:0:format=auto:eof_action=pass"
    if mode == "preview" and timecode and drawtext_works():
        start_s = run[0].out_frame / fps
        chain.append("drawtext=text='v%03d %%{pts\\:hms\\:%.3f}':x=10:y=10:fontsize=%d:fontcolor=white:"
                     "box=1:boxcolor=black@0.55:boxborderw=6" % (tl.meta.version, start_s, max(14, h // 30)))
    chain.append("format=yuv420p")
    script = scratch / f"{out_path.stem}.filter"
    script.write_text(graph + "," + ",".join(chain) + "[v]", encoding="utf-8")
    frames = sum(s.frames for s in run)
    args = ["-y", "-i", str(inp), *extra_inputs, "-filter_complex_script", str(script), "-map", "[v]", "-an",
            *_encoder_args(mode, encoder), "-r", fps_rational(fps), "-frames:v", str(frames),
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", str(out_path)]
    run_ffmpeg(args, desc=f"render video run ({len(run)} clips from {src.id})",
               duration=frames / fps, on_progress=on_progress)


def prepare_broll(job: Job, tl: Timeline, rng: tuple[float, float], w: int, h: int, scratch: Path) -> list[dict]:
    """Pre-render each cutaway in the range to exactly its frames at the output size: a still
    with a slow Ken Burns move (scaled up first, so the move has no pixel jitter), or a video
    clip conformed to the output rate, both filling the frame."""
    from cutroom.assets.library import asset_path, get_asset

    fps = tl.meta.fps
    out = []
    for i, b in enumerate(sorted(tl.broll, key=lambda b: b.start)):
        if b.end <= rng[0] or b.start >= rng[1]:
            continue
        f0 = int(round(b.start * fps)) - int(round(rng[0] * fps))
        frames = int(round(b.end * fps)) - int(round(b.start * fps))
        src = asset_path(get_asset(b.asset))
        dst = scratch / f"broll{i:03d}.mkv"
        if b.kind == "image":
            m = b.motion
            n = max(1, frames - 1)
            z = f"{m['scale0']}+({m['scale1'] - m['scale0']})*on/{n}"
            cx = f"{m['x0']}+({m['x1'] - m['x0']})*on/{n}"
            cy = f"{m['y0']}+({m['y1'] - m['y0']})*on/{n}"
            vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase:flags=lanczos,crop={w * 2}:{h * 2},"
                  f"zoompan=z='{z}':x='max(0,min(iw-iw/zoom,iw*({cx})-iw/zoom/2))':"
                  f"y='max(0,min(ih-ih/zoom,ih*({cy})-ih/zoom/2))':d=1:s={w}x{h}:fps={fps_rational(fps)},setsar=1")
            args = ["-y", "-loop", "1", "-framerate", fps_rational(fps), "-i", str(src), "-vf", vf,
                    "-frames:v", str(frames)]
        else:
            vf = (f"fps={fps_rational(fps)},scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                  f"crop={w}:{h},setsar=1")
            args = ["-y", "-ss", f"{b.src_in + max(0.0, rng[0] - b.start):.3f}", "-i", str(src), "-vf", vf, "-an",
                    "-frames:v", str(frames)]
        run_ffmpeg([*args, "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(dst)],
                   desc=f"b-roll {b.id}")
        out.append({"id": b.id, "path": str(dst), "f0": f0, "frames": frames})
    return out


def _graphics_key() -> str:
    """Hash of the Remotion sources: overlay frames drawn by other code must not be reused."""
    import hashlib

    from cutroom.graphics.remotion import graphics_dir

    h = hashlib.sha1()
    src = graphics_dir() / "src"
    for p in sorted(src.rglob("*")) if src.is_dir() else []:
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def _plan_key(tl: Timeline, mode, span, encoder, timecode, captions, audio_chain, join_fade) -> str:
    import hashlib

    from cutroom import __version__

    blob = json.dumps({"tl": tl.model_dump(mode="json", exclude={"meta": {"changelog", "created_at"}}), "mode": mode,
                       "span": span, "encoder": encoder, "timecode": timecode, "captions": captions,
                       "audio_chain": audio_chain, "join_fade": join_fade, "cutroom": __version__,
                       "graphics": _graphics_key()}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def _load_state(scratch: Path, key: str) -> dict:
    """Resume only an interrupted render of exactly the same plan; anything else starts clean."""
    import shutil

    st = scratch / "state.json"
    if st.is_file():
        try:
            state = json.loads(st.read_text(encoding="utf-8"))
        except ValueError:
            state = {}
        if state.get("key") == key:
            return state
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    state = {"key": key}
    _save_state(scratch, state)
    return state


def _save_state(scratch: Path, state: dict) -> None:
    atomic_write_text(scratch / "state.json", json.dumps(state, default=str) + "\n")


def _render_edit_audio(job: Job, tl: Timeline, segs, rng, wav: Path, scratch: Path, extra: dict, *,
                       join_fade: float, audio_chain: bool, seconds: float) -> tuple[list[int], Path]:
    """Dialogue (click-free joins) → voice chain → music/SFX beds with ducking. Returns the join
    sample indices and the WAV to mux; fills ``extra`` with the chain and mix reports."""
    joins = render_audio(job, tl, segs, wav, join_fade=join_fade)
    beds = render_beds(job, tl, rng)
    mixer = None
    if beds:
        from cutroom.audio.mix import mix_beds

        spans = speech_spans(job, tl, rng)
        mcfg = _style_value(tl, ("audio", "music"), {})

        def mixer(voice: Path, out: Path) -> dict:
            return mix_beds(voice, out, beds, spans, cfg=mcfg, scratch=scratch)
    if audio_chain and tl.audio.voice_chain:
        from cutroom.audio.chain import apply_chain

        out = scratch / "audio_processed.wav"
        extra["audio_chain"] = apply_chain(wav, out, tl.audio.voice_chain, duration=seconds, mix=mixer)
        if "mix" in extra["audio_chain"]:
            extra["mix"] = extra["audio_chain"].pop("mix")
        return joins, out
    if mixer:
        out = scratch / "audio_mixed.wav"
        extra["mix"] = mixer(wav, out)
        return joins, out
    return joins, wav


def _style_value(tl: Timeline, keys: tuple, default):
    from cutroom.style import timeline_style

    v = timeline_style(tl)
    for k in keys:
        v = v.get(k, {}) if isinstance(v, dict) else {}
    return v if v not in ({}, None) else default


def vertical_crop_filter(run: list[Segment], w: int, h: int) -> str:
    """A full-height window of the output's aspect, its centre following each clip's keyframes
    (piecewise linear in the run-local frame number ``n``), clamped inside the frame."""
    terms = []
    pos = 0
    for s in run:
        keys = s.vframe[1] if s.vframe and s.vframe[0] == "crop" else [(0, 0.5)]
        keys = sorted(keys)
        for i, (k, x) in enumerate(keys):
            a = pos + max(0, k) if i else pos
            b = pos + (keys[i + 1][0] if i + 1 < len(keys) else s.frames) - 1
            if b < a:
                continue
            if i + 1 < len(keys):
                x1 = keys[i + 1][1]
                span = max(1, b - a + 1)
                terms.append(f"between(n\\,{a}\\,{b})*({x:.5f}+({x1 - x:.5f})*(n-{a})/{span})")
            else:
                terms.append(f"between(n\\,{a}\\,{b})*({x:.5f})")
        pos += s.frames
    cx = "+".join(terms) or "0.5"
    return (f"crop=w='trunc(ih*{w}/{h}/2)*2':h='trunc(ih/2)*2':"
            f"x='max(0\\,min(iw-ow\\,iw*({cx})-ow/2))':y='(ih-oh)/2'")


def zoom_filter(run: list[Segment], fps: float, w: int, h: int) -> str | None:
    """One zoompan filter for a whole run: each clip's punch-in (or push) as a per-frame
    expression of the run-local frame number ``in``; clips without one stay at 100 %."""
    if not any(s.zoom for s in run):
        return None
    z, cx, cy = ["1"], ["0.5"], ["0.5"]
    pos = 0
    for s in run:
        if s.zoom:
            s0, x0, y0, s1, x1, y1 = s.zoom
            a, b = pos, pos + s.frames - 1
            span = max(1, b - a)
            on = f"between(in\\,{a}\\,{b})"
            lerp = lambda v0, v1, base: (f"{on}*({v0 - base:.5f})" if abs(v1 - v0) < 1e-6 else
                                         f"{on}*({v0 - base:.5f}+({v1 - v0:.5f})*(in-{a})/{span})")
            z.append(lerp(s0, s1, 1.0))
            cx.append(lerp(x0, x1, 0.5))
            cy.append(lerp(y0, y1, 0.5))
        pos += s.frames
    ze, xe, ye = "+".join(z), "+".join(cx), "+".join(cy)
    return (f"zoompan=z='{ze}':x='max(0\\,min(iw-iw/zoom\\,iw*({xe})-iw/zoom/2))'"
            f":y='max(0\\,min(ih-ih/zoom\\,ih*({ye})-ih/zoom/2))':d=1:s={w}x{h}:fps={fps_rational(fps)},"
            f"setpts=N/({fps_rational(fps)})/TB")


def output_shots(job: Job, tl: Timeline) -> list[dict]:
    """Shots as the viewer sees them: every join is a cut, and so is every source camera change
    inside a clip. Very short shots (< 0.4 s) look like flashes: a classic choppy-edit tell."""
    cuts = set()
    for c in tl.track_clips("V1"):
        cuts.add(round(c.start, 3))
        rep = job.root / "analysis" / c.source / "visual_report.json"
        if rep.is_file():
            for sh in json.loads(rep.read_text(encoding="utf-8")).get("shots", [])[1:]:
                if c.src_in + 0.01 < sh["start"] < c.src_out - 0.01:
                    cuts.add(round(c.to_timeline(sh["start"]), 3))
    edges = sorted(cuts | {round(tl.duration, 3)})
    return [{"start": a, "end": b, "duration": round(b - a, 3)} for a, b in zip(edges, edges[1:]) if b > a]


def overlay_frames(job: Job, tl: Timeline, w: int, h: int, total_frames: int, scratch: Path,
                   concurrency: int | None, *, captions: bool, span: tuple[float, float] | None,
                   on_progress=None, resume: bool = False) -> tuple[str, Path, dict]:
    """Render graphics and captions with Remotion in one pass; returns (FFmpeg pattern, frames dir, props).
    With ``resume``, frames already rendered by an interrupted render are kept and only the rest
    is drawn (the last frame found is redrawn: it may have been cut off)."""
    from cutroom.graphics.overlay import overlay_props
    from cutroom.graphics.remotion import frame_pattern, render_overlay
    from cutroom.style import timeline_style

    style_doc = timeline_style(tl)
    t0, t1 = span if span else (0.0, None)
    props = overlay_props(tl, style_doc, width=w, height=h, fps=tl.meta.fps, total_frames=total_frames,
                          job_root=job.root, captions=captions, t0=t0, t1=t1)
    out_dir = scratch / "overlay"
    have = sorted(out_dir.glob("*.png")) if resume and out_dir.is_dir() else []
    n = props["durationInFrames"]
    if have and len(have) >= n and (scratch / "overlay.done").is_file():
        pass                                              # complete from an earlier attempt
    elif have:
        nums = sorted(int(p.stem) for p in have)
        first_missing = next((k for k, v in enumerate(nums) if k != v), len(nums))
        start = max(0, first_missing - 1)                 # redraw the last one: it may be truncated
        for p in have:
            if int(p.stem) >= start:
                p.unlink()
        render_overlay(props, out_dir, composition="Overlay", concurrency=concurrency, on_progress=on_progress,
                       scratch=scratch, frame_range=(start, n - 1), keep_existing=True)
    else:
        render_overlay(props, out_dir, composition="Overlay", concurrency=concurrency, on_progress=on_progress,
                       scratch=scratch)
    (scratch / "overlay.done").write_text("ok", encoding="utf-8")
    pattern, _ = frame_pattern(out_dir)
    return pattern, out_dir, props


def faces_at(job: Job, tl: Timeline, t0: float = 0.0):
    """faces(t) → face boxes at output time t (nearest 1 s analysis sample, same clip)."""
    data = {}
    for s in tl.sources:
        p = job.root / "analysis" / s.id / "faces.json"
        if p.is_file():
            data[s.id] = json.loads(p.read_text(encoding="utf-8"))
    clips = tl.track_clips()
    from cutroom.shorts.reframe import output_face_mapper

    to_out = output_face_mapper(tl)

    def faces(t: float) -> list[dict]:
        t += t0
        for c in clips:
            if c.start <= t < c.end and c.source in data:
                st = c.to_source(t)
                near = [smp for smp in data[c.source] if abs(smp["t"] - st) <= 0.6 and c.src_in - 0.3 <= smp["t"] <= c.src_out + 0.3]
                found = min(near, key=lambda smp: abs(smp["t"] - st))["faces"] if near else []
                return [m for m in (to_out(t, f) for f in found) if m]
        return []
    return faces


def check_graphics(frames_dir: Path, props: dict, faces, *, every: int = 6) -> dict:
    """Measure drawn graphics: share of each head covered and pixels outside the graphics safe
    area, on frames where a placed graphic (lower third, counter, list…) is fully in. Caption
    pixels are excluded by masking the caption band."""
    import cv2

    from cutroom.graphics.placement import head_box

    w, h = props["width"], props["height"]
    safe = props["theme"].get("safe", {"left": 0.06, "right": 0.94, "top": 0.07, "bottom": 0.93})
    pad = 0.005
    cap = props.get("captions")
    width = len(next(iter(sorted(frames_dir.glob("*.png")))).stem)
    results = {}
    for g in props["graphics"]:
        anchor = (g.get("placement") or {}).get("anchor", "center-center")
        if anchor == "center-center":
            continue   # full-frame by design (title cards, transitions, callouts)
        f_in, f_out = max(0, g["from"] + 14), min(props["durationInFrames"], g["to"] - 10)
        if f_out <= f_in:
            continue   # only its entrance or exit is in this (range) render
        worst, outside, checked = 0.0, 0, 0
        for f in range(f_in, f_out, every):
            img = cv2.imread(str(frames_dir / f"{f:0{width}d}.png"), cv2.IMREAD_UNCHANGED)
            if img is None or img.shape[2] < 4:
                continue
            alpha = img[:, :, 3] > 128     # solid pixels: panels and text, not their soft drop shadows
            others = [o for o in props["graphics"] if o is not g and o["from"] <= f < o["to"]]
            if any((o.get("placement") or {}).get("anchor", "center-center") == "center-center"
                   and o["component"] != "ProgressBar" for o in others):
                continue        # a full-frame graphic is on screen too: pixels cannot be told apart
            for o in others:    # keep only this graphic's pixels
                if o["component"] == "ProgressBar":
                    strip = int(0.03 * h)
                    if o.get("variant") == "top":
                        alpha[:strip, :] = False
                    else:
                        alpha[h - strip:, :] = False
                elif (o.get("placement") or {}).get("box"):
                    x0, y0, x1, y1 = o["placement"]["box"]
                    alpha[max(0, int((y0 - 0.02) * h)):int((y1 + 0.02) * h), max(0, int((x0 - 0.02) * w)):int((x1 + 0.02) * w)] = False
            if cap:   # mask the caption block on screen at this frame
                st = cap["style"]
                for c in cap["cues"]:
                    if c["from"] <= f < c["to"]:
                        block = (len(c["lines"]) * st["fontSize"] * st.get("lineHeight", 1.18)
                                 + 2 * st.get("box", {}).get("padY", 0)) / h + 0.02
                        if c["placement"] == "top":
                            y0, y1 = cap["safe"]["top"] - 0.02, cap["safe"]["top"] + block + 0.005
                        else:
                            y0, y1 = cap["safe"]["bottom"] - block - 0.005, cap["safe"]["bottom"] + 0.02
                        alpha[max(0, int(y0 * h)):int(y1 * h), :] = False
            checked += 1
            ys, xs = np.nonzero(alpha)
            if len(xs) and (xs.min() < (safe["left"] - pad) * w or xs.max() > (safe["right"] + pad) * w
                            or ys.min() < (safe["top"] - pad) * h or ys.max() > (safe["bottom"] + pad) * h):
                outside += 1
            for fb in faces(f / props["fps"]):
                x0, y0, x1, y1 = head_box(fb)
                X0, Y0, X1, Y1 = max(0, int(x0 * w)), max(0, int(y0 * h)), min(w, int(x1 * w)), min(h, int(y1 * h))
                if X1 > X0 and Y1 > Y0:
                    worst = max(worst, float(alpha[Y0:Y1, X0:X1].mean()))
        results[g["id"]] = {"frames_checked": checked, "head_covered": round(worst, 3),
                            "frames_outside_safe_area": outside}
    return results


def check_caption_safe_zone(frames_dir: Path, props: dict, *, every: int = 15, tolerance: float = 0.01) -> dict:
    """Measure where caption pixels were actually drawn (alpha > 0.25) on sampled frames and
    check they stay inside the safe zone. Shadows are faint and ignored by the threshold."""
    import cv2

    safe = props["safe"]
    w, h = props["width"], props["height"]
    lo_x, hi_x = (safe["left"] - tolerance) * w, (safe["right"] + tolerance) * w
    lo_y, hi_y = (safe["top"] - tolerance) * h, (safe["bottom"] + tolerance) * h
    width = len(next(iter(sorted(frames_dir.glob("*.png")))).stem)
    busy = props.get("busy_frames", [])
    sample = sorted(f for f in ({c["from"] + (c["to"] - c["from"]) // 2 for c in props["cues"]} |
                                set(range(0, props["durationInFrames"], every)))
                    if not any(a <= f < b for a, b in busy))
    checked, outside = 0, []
    for f in sample:
        img = cv2.imread(str(frames_dir / f"{f:0{width}d}.png"), cv2.IMREAD_UNCHANGED)
        if img is None or img.shape[2] < 4:
            continue
        alpha = img[:, :, 3] > 64
        for strip in props.get("mask_strips", []):
            k = int(0.03 * h)
            alpha[(slice(0, k) if strip == "top" else slice(h - k, h)), :] = False
        ys, xs = np.nonzero(alpha)
        if not len(xs):
            continue
        checked += 1
        box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        if box[0] < lo_x or box[2] > hi_x or box[1] < lo_y or box[3] > hi_y:
            outside.append({"frame": f, "box": box})
    return {"frames_with_captions_checked": checked, "outside_safe_zone": outside[:20],
            "outside_count": len(outside)}


def caption_sync_ok(sync: dict) -> bool:
    """95 % of measurable captions within 80 ms. With fewer than 20 measurable captions (a Short),
    one outlier would decide the verdict, so one miss up to 200 ms is tolerated there."""
    n = sync.get("measured", 0)
    if not n:
        return True
    if sync["within_80ms"] >= 0.95:
        return True
    misses = round(n * (1 - sync["within_80ms"]))
    return n < 20 and misses <= 1 and sync.get("max_ms", 1e9) <= 200


def check_caption_sync(wav: Path, props: dict, *, max_ms: float = 80.0, joins_s: list[float] | None = None) -> dict:
    """When a caption starts after a pause, does it appear when the speech starts?

    Only captions whose first word follows a measurable pause are checked, and only a pause
    that lies after the previous caption's last word (a pause earlier in the phrase says
    nothing about this caption). Compares the caption's first visible frame with the speech
    onset (end of that pause) in the rendered audio.
    """
    from cutroom.perception.audio import envelope_db, levels, read_mono
    from cutroom.perception.words import silence_runs

    x, sr = read_mono(wav)
    env = envelope_db(x, sr)
    _, _, thr = levels(env)
    sil = silence_runs(env, thr, 0.12)
    fps = props["fps"]
    diffs = []
    quiet = at_join = trimmed = 0
    joins_s = sorted(joins_s or [])
    prev_end = 0.0
    for c in props["cues"]:
        if c.get("trimmed"):
            # Starts late on purpose: a graphic showing these words hid it (kinetic text).
            trimmed += 1
            prev_end = c["lines"][-1][-1]["to"] / fps
            continue
        word_t = c["lines"][0][0]["from"] / fps
        w_end = max(word_t + 0.03, min(c["lines"][0][0]["to"] / fps, word_t + 0.15))
        i0, i1 = int(word_t / 0.005), int(w_end / 0.005)
        if i1 > i0 and float(np.max(env[i0:i1])) < thr + 3.0:
            # The first word is barely above the noise ("But" mumbled before a pause): its onset
            # cannot be measured, and the next loud word would be mistaken for it.
            quiet += 1
            prev_end = c["lines"][-1][-1]["to"] / fps
            continue
        # The pause must lie between the previous caption's last word and this caption's first
        # word: it starts before the word and ends near it (not a pause after a short first word).
        cands = [b for a, b in sil if a >= prev_end - 0.03 and a < word_t and abs(b - word_t) <= 0.3]
        prev_end = c["lines"][-1][-1]["to"] / fps
        if not cands:
            continue
        onset = min(cands, key=lambda b: abs(b - word_t))
        if any(abs(onset - j) <= 0.04 for j in joins_s):
            # The "onset" is an edit join: the kept lead-in (often an inhale) starts there, so
            # breath and speech cannot be told apart at this point.
            at_join += 1
            continue
        diffs.append({"t": round(c["from"] / fps, 3), "onset": round(onset, 3),
                      "ms": round((c["from"] / fps - onset) * 1000, 1)})
    if not diffs:
        return {"measured": 0, "skipped_quiet_first_word": quiet, "skipped_onset_at_join": at_join,
                "skipped_hidden_by_graphic": trimmed}
    ad = sorted(abs(d["ms"]) for d in diffs)
    within = sum(d <= max_ms for d in ad) / len(ad)
    return {"measured": len(ad), "skipped_quiet_first_word": quiet, "skipped_onset_at_join": at_join,
            "skipped_hidden_by_graphic": trimmed,
            "median_ms": ad[len(ad) // 2],
            "p95_ms": ad[int(0.95 * (len(ad) - 1))],
            "max_ms": ad[-1], "within_80ms": round(within, 3),
            "worst": sorted(diffs, key=lambda d: -abs(d["ms"]))[:5]}


def render(job: Job, tl: Timeline, *, mode: Mode = "preview", out_path: Path | None = None,
           encoder: str = "libx264", timecode: bool = True, join_fade: float = 0.008,
           captions: bool = True, audio_chain: bool = True, graphics_concurrency: int | None = None,
           span: tuple[float, float] | None = None,
           on_progress: Callable[[float], None] | None = None) -> dict:
    """Render ``tl`` (or only timeline seconds ``span``); returns a report with self-checks."""
    t0 = time.perf_counter()
    segs = plan_segments(tl)
    fps = tl.meta.fps
    suffix = ""
    if span is not None:
        a, b = max(0.0, span[0]), min(tl.duration, span[1])
        if b <= a:
            raise CutroomError(f"Empty range {span[0]}-{span[1]} (timeline is {tl.duration:.2f}s).", fix="Check the range.")
        segs = clip_segments(segs, int(round(a * fps)), int(round(b * fps)))
        if tl.captions:
            tl = tl.model_copy(deep=True)
            tl.captions = shift_captions(tl.captions, a, b)
        suffix = f"_{a:g}-{b:g}"
    if not segs:
        raise CutroomError("The timeline is empty.", fix="Check `cutroom show` and restore some cuts.")
    total_frames = sum(s.frames for s in segs)
    renders = job.root / "renders"
    renders.mkdir(exist_ok=True)
    out_path = out_path or renders / f"v{tl.meta.version:03d}_{mode}{suffix}.mp4"
    scratch = renders / f".tmp_v{tl.meta.version:03d}_{mode}{suffix}"
    key = _plan_key(tl, mode, span, encoder, timecode, captions, audio_chain, join_fade)
    state = _load_state(scratch, key)
    resuming = bool(state.get("stages"))
    extra: dict = {"resumed": resuming} if resuming else {}
    timings: dict = {}
    clock = [time.perf_counter()]

    def lap(stage: str) -> None:
        now = time.perf_counter()
        timings[stage] = round(timings.get(stage, 0.0) + now - clock[0], 1)
        clock[0] = now

    def mark(stage: str, **data) -> None:
        state.setdefault("stages", {})[stage] = data or True
        _save_state(scratch, state)
    w, h = output_size(tl, mode)
    use_caps = bool(captions and tl.captions and tl.captions.cues and tl.captions.burn_in)
    rng = (a, b) if span is not None else (0.0, tl.duration)
    use_gfx = any(g.end > rng[0] and g.start < rng[1] for g in tl.graphics)
    cap_share = 0.45 if (use_caps or use_gfx) else 0.0  # share of the progress bar for the overlay
    try:
        wav = scratch / "audio.wav"
        done_audio = state.get("stages", {}).get("audio")
        if done_audio and wav.is_file() and Path(done_audio["mux"]).is_file():
            joins, mux_wav = done_audio["joins"], Path(done_audio["mux"])   # from an interrupted attempt
            extra.update(done_audio.get("extra", {}))
        else:
            joins, mux_wav = _render_edit_audio(job, tl, segs, rng, wav, scratch, extra, join_fade=join_fade,
                                                audio_chain=audio_chain, seconds=total_frames / fps)
            mark("audio", joins=list(joins), mux=str(mux_wav),
                 extra={k: extra[k] for k in ("audio_chain", "mix") if k in extra})
        lap("audio")
        overlay = None
        if use_caps or use_gfx:
            from cutroom.graphics.overlay import caption_check_props

            overlay, ov_dir, props = overlay_frames(
                job, tl, w, h, total_frames, scratch, graphics_concurrency, captions=use_caps,
                span=(a, b) if span is not None else None, resume=resuming,
                on_progress=(lambda f: on_progress(f * cap_share)) if on_progress else None)
            lap("overlay")
            cprops = caption_check_props(props)
            if cprops:
                extra["captions"] = {**tl.captions.stats, "style": tl.captions.style, "cues": len(tl.captions.cues),
                                     "hidden_by_graphics": props["captions_hidden_by_graphics"],
                                     "safe_zone": check_caption_safe_zone(ov_dir, cprops),
                                     # Timing is a property of the words: measure on the unprocessed edit
                                     # audio (compression changes onset thresholds, not timing).
                                     "sync": check_caption_sync(wav, cprops, joins_s=[j / SR for j in joins])}
            if props["graphics"]:
                extra["graphics"] = {"count": len(props["graphics"]),
                                     "measured": check_graphics(ov_dir, props, faces_at(job, tl, rng[0]))}
        lap("checks")
        done_broll = state.get("stages", {}).get("broll")
        if done_broll and all(Path(b["path"]).is_file() for b in done_broll):
            broll = done_broll                              # pre-rendered by an interrupted attempt
        else:
            broll = prepare_broll(job, tl, rng, w, h, scratch) if tl.broll else []
            if broll:
                state["stages"]["broll"] = broll
                _save_state(scratch, state)
        if broll:
            extra["broll"] = [{"id": b["id"], "frames": b["frames"]} for b in broll]
            lap("broll")
        runs = group_runs(segs)
        parts = []
        done = 0
        for i, run in enumerate(runs):
            part = scratch / f"run{i:03d}.mkv"
            frames = sum(s.frames for s in run)

            def prog(f: float, base=done, n=frames) -> None:
                if on_progress:
                    on_progress(min(0.99, cap_share + (1 - cap_share) * (base + f * n) / total_frames))

            if i in state.get("runs_done", []) and part.is_file():
                parts.append(part)                       # encoded by an interrupted attempt
                done += frames
                continue
            render_run(job, tl, run, part, mode=mode, encoder=encoder, timecode=timecode, scratch=scratch,
                       on_progress=prog, overlay=overlay, broll=broll)
            parts.append(part)
            done += frames
            state.setdefault("runs_done", []).append(i)
            _save_state(scratch, state)
        lap("video")
        concat = scratch / "concat.txt"
        concat.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        abitrate = "384k" if mode == "final" else "160k"
        tmp_out = out_path.with_name(out_path.stem + ".partial.mp4")
        # YouTube asks for MP4s without edit lists. Video: negative composition offsets keep the
        # first frame at t=0 without one. Audio: the AAC encoder prepends AAC_PRIMING samples of
        # priming; dropping the same number from the start of our audio (always head-pad
        # silence) keeps the decoded audio aligned with the picture without an edit list.
        run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-i", str(mux_wav), "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-af", f"atrim=start_sample={AAC_PRIMING},asetpts=PTS-STARTPTS",
                    "-c:a", "aac", "-b:a", abitrate, "-ar", str(SR), "-ac", "2",
                    "-use_editlist", "0", "-movflags", "+faststart+negative_cts_offsets", str(tmp_out)], desc="mux")
        tmp_out.replace(out_path)
        clicks = detect_clicks(wav, joins)
        lap("mux")
    except BaseException:
        # Keep the finished work: the same command resumes (audio, overlay frames, encoded runs).
        msg = (f"render of v{tl.meta.version:03d} {mode}{suffix} interrupted; finished parts kept in "
               f"{scratch.relative_to(job.root)} — run the same command again to resume")
        job.log.warn(msg)
        print(msg[0].upper() + msg[1:] + ".", file=sys.stderr, flush=True)
        raise
    import shutil

    shutil.rmtree(scratch, ignore_errors=True)
    info = probe(out_path)
    expected = total_frames / fps
    v, a = info.primary_video, info.primary_audio
    report = {
        "version": tl.meta.version, "mode": mode, "path": str(out_path.relative_to(job.root)),
        "size": f"{v.display_width}x{v.display_height}" if v else None, "fps": v.fps if v else None,
        "expected_duration_s": round(expected, 3), "duration_s": round(info.duration, 3),
        "video_frames": v.frames if v else None, "expected_frames": total_frames,
        "audio_duration_s": round(a.duration, 3) if a and a.duration else None,
        "clips": len(segs), "joins": len(joins), "runs": len(runs), "clicks": clicks,
        "flash_shots": [x for x in output_shots(job, tl) if x["duration"] < 0.4] if span is None else [],
        "range": list(span) if span else None,
        "elapsed_s": round(time.perf_counter() - t0, 1), "ffmpeg": ffmpeg_info().version,
        "encoder": "libx264" if mode == "final" else encoder,
        **extra,
        "timings_s": timings,
    }
    from cutroom.perception.audio import measure_loudness

    loud = measure_loudness(out_path)
    report["loudness"] = {"integrated_lufs": loud.integrated_lufs, "true_peak_dbtp": loud.true_peak_dbtp,
                          "lra_lu": loud.lra_lu}
    report["checks"] = {
        "no_edit_lists": not has_edit_list(out_path),
        "duration_ok": abs(info.duration - expected) <= 2 / fps + 0.03,
        "frames_ok": v is not None and (v.frames is None or abs(v.frames - total_frames) <= 1),
        "no_clicks": not clicks,
    }
    chain = tl.audio.voice_chain if audio_chain else None
    if chain:
        target, tp = chain.get("target_lufs", -14.0), chain.get("true_peak_dbtp", -1.0)
        report["checks"]["loudness_on_target"] = (loud.integrated_lufs is not None
                                                  and abs(loud.integrated_lufs - target) <= 1.0)
        report["checks"]["true_peak_ok"] = loud.true_peak_dbtp is not None and loud.true_peak_dbtp <= tp
    if "graphics" in extra:
        m = extra["graphics"]["measured"]
        # A check that measured nothing must not pass: every placed graphic needs sampled frames.
        report["checks"]["graphics_off_faces"] = all(v["head_covered"] <= 0.02 and v["frames_checked"] > 0
                                                     for v in m.values())
        report["checks"]["graphics_in_safe_area"] = all(v["frames_outside_safe_area"] == 0 for v in m.values())
    if "captions" in extra:
        c = extra["captions"]
        report["checks"]["captions_in_safe_zone"] = (c["safe_zone"]["outside_count"] == 0
                                                     and c["safe_zone"]["frames_with_captions_checked"] > 0)
        if c["sync"].get("measured"):
            report["checks"]["caption_sync_80ms"] = caption_sync_ok(c["sync"])
    atomic_write_text(out_path.with_suffix(".json"), json.dumps(report, indent=1) + "\n")
    if on_progress:
        on_progress(1.0)
    return report
