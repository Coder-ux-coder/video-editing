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
        segs.append(Segment(c.id, c.source, n_in, n_out, pos))
        pos += n_out - n_in
    return segs


def clip_segments(segs: list[Segment], f0: int, f1: int) -> list[Segment]:
    """Keep only output frames [f0, f1) and renumber them from 0 (for range renders)."""
    out = []
    for s in segs:
        a, b = max(s.out_frame, f0), min(s.out_frame + s.frames, f1)
        if b <= a:
            continue
        n_in = s.n_in + (a - s.out_frame)
        out.append(Segment(s.clip_id, s.source, n_in, n_in + (b - a), a - f0))
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
        if runs and runs[-1][-1].source == s.source and s.n_in >= runs[-1][-1].n_out:
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
               overlay: str | None = None) -> None:
    src = job.source(run[0].source)
    fps = tl.meta.fps
    use_proxy = mode == "preview" and src.proxy and job.abspath(src.proxy).is_file()
    inp = job.abspath(src.proxy) if use_proxy else job.source_path(src)
    w, h = output_size(tl, mode)
    sel = "+".join(f"between(n\\,{s.n_in}\\,{s.n_out - 1})" for s in run)
    chain = [f"fps={fps_rational(fps)}", f"select='{sel}'", f"setpts=N/({fps_rational(fps)})/TB",
             f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bicubic",
             f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2", "setsar=1"]
    graph = "[0:v]" + ",".join(chain)
    chain = []
    extra_inputs: list[str] = []
    if overlay:
        # Transparent caption/graphics frames numbered in output-timeline frames: this run reads
        # exactly its own frames, starting at its first output frame.
        extra_inputs = ["-framerate", fps_rational(fps), "-start_number", str(run[0].out_frame), "-i", overlay]
        graph += "[base];[1:v]format=rgba[ov];[base][ov]overlay=0:0:format=auto:eof_action=pass"
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


def caption_overlay(job: Job, tl: Timeline, w: int, h: int, total_frames: int, scratch: Path,
                    concurrency: int | None, on_progress=None) -> tuple[str, Path, dict]:
    """Render the timeline's captions with Remotion; returns (FFmpeg pattern, frames dir, props)."""
    from cutroom.captions.plan import to_remotion_props
    from cutroom.graphics.remotion import frame_pattern, render_overlay
    from cutroom.style import load_style

    style_doc = load_style(tl.meta.style, client=tl.meta.client)
    props = to_remotion_props(tl.captions, style_doc, width=w, height=h, fps=tl.meta.fps,
                              duration=total_frames / tl.meta.fps)
    props["durationInFrames"] = max(props["durationInFrames"], total_frames)
    out_dir = scratch / "captions"
    render_overlay(props, out_dir, composition="Captions", concurrency=concurrency, on_progress=on_progress,
                   scratch=scratch)
    pattern, _ = frame_pattern(out_dir)
    return pattern, out_dir, props


def check_caption_safe_zone(frames_dir: Path, props: dict, *, every: int = 15, tolerance: float = 0.01) -> dict:
    """Measure where caption pixels were actually drawn (alpha > 0.25) on sampled frames and
    check they stay inside the safe zone. Shadows are faint and ignored by the threshold."""
    import cv2

    safe = props["safe"]
    w, h = props["width"], props["height"]
    lo_x, hi_x = (safe["left"] - tolerance) * w, (safe["right"] + tolerance) * w
    lo_y, hi_y = (safe["top"] - tolerance) * h, (safe["bottom"] + tolerance) * h
    width = len(next(iter(sorted(frames_dir.glob("*.png")))).stem)
    sample = sorted({c["from"] + (c["to"] - c["from"]) // 2 for c in props["cues"]} |
                    set(range(0, props["durationInFrames"], every)))
    checked, outside = 0, []
    for f in sample:
        img = cv2.imread(str(frames_dir / f"{f:0{width}d}.png"), cv2.IMREAD_UNCHANGED)
        if img is None or img.shape[2] < 4:
            continue
        ys, xs = np.nonzero(img[:, :, 3] > 64)
        if not len(xs):
            continue
        checked += 1
        box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        if box[0] < lo_x or box[2] > hi_x or box[1] < lo_y or box[3] > hi_y:
            outside.append({"frame": f, "box": box})
    return {"frames_with_captions_checked": checked, "outside_safe_zone": outside[:20],
            "outside_count": len(outside)}


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
    quiet = at_join = 0
    joins_s = sorted(joins_s or [])
    prev_end = 0.0
    for c in props["cues"]:
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
        return {"measured": 0, "skipped_quiet_first_word": quiet, "skipped_onset_at_join": at_join}
    ad = sorted(abs(d["ms"]) for d in diffs)
    within = sum(d <= max_ms for d in ad) / len(ad)
    return {"measured": len(ad), "skipped_quiet_first_word": quiet, "skipped_onset_at_join": at_join,
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
    scratch.mkdir(exist_ok=True)
    extra: dict = {}
    w, h = output_size(tl, mode)
    use_caps = bool(captions and tl.captions and tl.captions.cues and tl.captions.burn_in)
    cap_share = 0.45 if use_caps else 0.0  # share of the progress bar for the caption overlay
    try:
        wav = scratch / "audio.wav"
        joins = render_audio(job, tl, segs, wav, join_fade=join_fade)
        mux_wav = wav
        if audio_chain and tl.audio.voice_chain:
            from cutroom.audio.chain import apply_chain

            mux_wav = scratch / "audio_processed.wav"
            extra["audio_chain"] = apply_chain(wav, mux_wav, tl.audio.voice_chain, duration=total_frames / fps)
        overlay = None
        if use_caps:
            overlay, cap_dir, props = caption_overlay(
                job, tl, w, h, total_frames, scratch, graphics_concurrency,
                on_progress=(lambda f: on_progress(f * cap_share)) if on_progress else None)
            extra["captions"] = {**tl.captions.stats, "style": tl.captions.style, "cues": len(tl.captions.cues),
                                 "safe_zone": check_caption_safe_zone(cap_dir, props),
                                 # Timing is a property of the words: measure on the unprocessed edit
                                 # audio (compression changes onset thresholds, not timing).
                                 "sync": check_caption_sync(wav, props, joins_s=[j / SR for j in joins])}
        runs = group_runs(segs)
        parts = []
        done = 0
        for i, run in enumerate(runs):
            part = scratch / f"run{i:03d}.mkv"
            frames = sum(s.frames for s in run)

            def prog(f: float, base=done, n=frames) -> None:
                if on_progress:
                    on_progress(min(0.99, cap_share + (1 - cap_share) * (base + f * n) / total_frames))

            render_run(job, tl, run, part, mode=mode, encoder=encoder, timecode=timecode, scratch=scratch,
                       on_progress=prog, overlay=overlay)
            parts.append(part)
            done += frames
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
    finally:
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
    if "captions" in extra:
        c = extra["captions"]
        report["checks"]["captions_in_safe_zone"] = c["safe_zone"]["outside_count"] == 0
        if c["sync"].get("measured"):
            report["checks"]["caption_sync_80ms"] = c["sync"]["within_80ms"] >= 0.95
    atomic_write_text(out_path.with_suffix(".json"), json.dumps(report, indent=1) + "\n")
    if on_progress:
        on_progress(1.0)
    return report
