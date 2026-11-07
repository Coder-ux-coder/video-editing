"""Caption and audio-polish commands: captions, export-captions, audio, loudness."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Optional

import numpy as np
import typer

from cutroom.cli_common import JobArg, JsonOpt, VersionOpt, emit, out
from cutroom.errors import JobError
from cutroom.job import Job, atomic_write_text
from cutroom.timeline import store
from cutroom.timeline.outline import tc
from cutroom.tools._common import open_edit
from cutroom.tools.registry import ToolManifest, tool

TESTS = ["tests/test_captions.py"]


def _faces_lookup(job: Job, tl):
    """faces(t0, t1) → main face boxes sampled inside that timeline span."""
    data = {}
    for s in tl.sources:
        p = job.root / "analysis" / s.id / "faces.json"
        if p.is_file():
            data[s.id] = json.loads(p.read_text(encoding="utf-8"))

    def faces(t0: float, t1: float) -> list[dict]:
        found = []
        for c in tl.track_clips():
            if c.end <= t0 or c.start >= t1 or c.source not in data:
                continue
            a, b = c.to_source(max(t0, c.start)), c.to_source(min(t1, c.end))
            found += [smp["faces"][0] for smp in data[c.source] if a <= smp["t"] <= b and smp["faces"]]
        return found
    return faces


def _brightness_lookup(job: Job, tl):
    """brightness(t, y0, y1[, x0, x1]) → 75th-percentile WCAG relative luminance of that region at timeline t."""
    from cutroom.perception import visual as V

    @lru_cache(maxsize=512)
    def frame(sid: str, t: float):
        s = job.source(sid)
        if not s.proxy:
            return None
        return V.grab_frames(job.abspath(s.proxy), [t], 180)[0]

    def brightness(t: float, y0: float, y1: float, x0: float = 0.1, x1: float = 0.9, pct: float = 75) -> float | None:
        c = next((c for c in tl.track_clips() if c.start <= t < c.end), None)
        if c is None:
            return None
        img = frame(c.source, round(c.to_source(t), 1))
        if img is None:
            return None
        h, w = img.shape[:2]
        band = img[int(max(0, y0) * h): int(min(1, y1) * h), int(max(0, x0) * w): int(min(1, x1) * w)].astype(np.float32) / 255
        if band.size == 0:
            return None
        lin = np.where(band <= 0.04045, band / 12.92, ((band + 0.055) / 1.055) ** 2.4)
        lum = 0.0722 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.2126 * lin[..., 2]   # BGR order
        return float(np.percentile(lum, pct))
    return brightness


@tool(ToolManifest("captions", "Plan captions (phrases, timing, emphasis, placement) in a caption style.", "graphics",
                   inputs=["timeline HEAD", "words.json", "faces.json", "proxy frames", "style captions.*"],
                   outputs=["timeline vN+1 with captions"], tests=TESTS, changes_timeline=True))
def captions(job_id: JobArg,
             style: Annotated[Optional[str], typer.Option(help="clean, bold-karaoke, word-highlight or minimal (default from style file).")] = None,
             output: Annotated[Literal["youtube_long", "youtube_short"], typer.Option(help="Safe zone and line length to plan for.")] = "youtube_long",
             burn_in: Annotated[bool, typer.Option("--burn-in/--files-only", help="Draw captions into the video, or only export caption files.")] = True,
             version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Plan captions from the refined word timings: phrases break at sentence ends, commas and
    pauses (never after "the", "of", ...), at most 2 lines, reading speed checked, at most one
    emphasised word, kept off faces and inside the platform safe zone, with an automatic scrim
    when the background is too bright for contrast. Saves a new timeline version."""
    from cutroom.captions.plan import plan_captions

    ctx = open_edit(job_id, version)
    name = style or ctx.style["captions"]["default_style"]
    if name not in ctx.style["captions"]["styles"]:
        raise JobError(f"Unknown caption style '{name}'.",
                       fix=f"Choose one of: {', '.join(ctx.style['captions']['styles'])}")
    caps = plan_captions(ctx.tl, ctx.words, ctx.style, name, output=output,
                         faces=_faces_lookup(ctx.job, ctx.tl), brightness=_brightness_lookup(ctx.job, ctx.tl))
    caps.burn_in = burn_in
    tl = ctx.tl.model_copy(deep=True)
    tl.captions = caps
    st = caps.stats
    summary = (f"captions '{name}' ({output}): {st['cues']} captions, median {st['median_cps']} chars/s, "
               f"{st['over_target_cps']} over {ctx.style['captions']['max_cps']} chars/s, {st['scrims']} with scrim, "
               f"{st['placed_top']} moved off faces")
    ctx.job.log.section(f"captions (from v{ctx.base:03d})")
    n = store.save_new(ctx.job, tl, command="captions", summary=summary, parent=ctx.base)
    emit(as_json, {"version": n, "style": name, "stats": st})
    if not as_json:
        out.print(f"{summary} → v{n:03d}")
        for c in caps.cues[:5]:
            lines = " / ".join(" ".join(c.words[i]["text"] for i in line) for line in c.lines)
            out.print(f"  [{tc(c.start)} → {tc(c.end)}] {lines}", markup=False, highlight=False)


@tool(ToolManifest("export-captions", "Write .srt and .vtt caption files for a timeline version.", "graphics",
                   inputs=["timeline version with captions"], outputs=["renders/vNNN.srt", "renders/vNNN.vtt"],
                   tests=TESTS))
def export_captions(job_id: JobArg, version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Export the planned captions as SubRip (.srt) and WebVTT (.vtt), in edit time, ready to
    upload to YouTube. Always deliver these, even when captions are burned in."""
    from cutroom.captions.export import to_srt, to_vtt

    job = Job.load(job_id)
    tl = store.load(job, version)
    if not tl.captions or not tl.captions.cues:
        raise JobError(f"v{tl.meta.version:03d} has no captions.", fix=f"Run `cutroom captions {job.id}` first.")
    base = job.root / "renders" / f"v{tl.meta.version:03d}"
    base.parent.mkdir(exist_ok=True)
    atomic_write_text(base.with_suffix(".srt"), to_srt(tl.captions))
    atomic_write_text(base.with_suffix(".vtt"), to_vtt(tl.captions))
    res = {"srt": str(base.with_suffix(".srt").relative_to(job.root)),
           "vtt": str(base.with_suffix(".vtt").relative_to(job.root)), "cues": len(tl.captions.cues)}
    job.log.note(f"Caption files exported: `{res['srt']}`, `{res['vtt']}` ({res['cues']} captions).")
    emit(as_json, res)
    if not as_json:
        out.print(f"Wrote {res['srt']} and {res['vtt']} ({res['cues']} captions)")


@tool(ToolManifest("audio", "Plan the voice chain and loudness target from the audio analysis.", "audio",
                   inputs=["timeline HEAD", "analysis/<src>/audio_report.json", "style audio.*"],
                   outputs=["timeline vN+1 with audio.voice_chain"], tests=["tests/test_audio.py"],
                   changes_timeline=True))
def audio(job_id: JobArg,
          target_lufs: Annotated[Optional[float], typer.Option(help="Integrated loudness target (default from style: -14).")] = None,
          denoise: Annotated[Optional[bool], typer.Option("--denoise/--no-denoise", help="Force denoise on/off (default: decided from the noise measurement).")] = None,
          version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Decide the voice chain (high-pass, denoise only if needed, gentle EQ, compression set
    from the measured speech level, de-essing) and the loudness target, with a reason for each
    stage. Renders then apply it and verify loudness and true peak on the output file."""
    from cutroom.audio.chain import plan_chain
    from cutroom.perception.pipeline import audio_report

    ctx = open_edit(job_id, version, need_words=False)
    main = ctx.tl.sources[0]
    rep = audio_report(ctx.job, ctx.job.source(main.id))
    chain = plan_chain(rep, ctx.style)
    if target_lufs is not None:
        chain["target_lufs"] = target_lufs
    if denoise is False:
        chain.pop("denoise", None)
        chain["reasons"].append("denoise disabled by request")
    elif denoise and "denoise" not in chain:
        chain["denoise"] = {"nf": max(-80, min(-20, round((rep.get("noise_floor_dbfs") or -60) + 3))), "nr": 6}
        chain["reasons"].append("denoise 6 dB by request")
    tl = ctx.tl.model_copy(deep=True)
    tl.audio.voice_chain = chain
    tl.audio.target_lufs = chain["target_lufs"]
    tl.audio.true_peak_dbtp = chain["true_peak_dbtp"]
    ctx.job.log.section(f"audio (from v{ctx.base:03d})")
    for r in chain["reasons"]:
        ctx.job.log.decision("voice chain", r)
    n = store.save_new(ctx.job, tl, command="audio",
                       summary=f"voice chain planned ({len(chain['reasons'])} stages), target {chain['target_lufs']} LUFS",
                       parent=ctx.base)
    emit(as_json, {"version": n, "chain": chain})
    if not as_json:
        for r in chain["reasons"]:
            out.print(f"  · {r}")
        out.print(f"→ v{n:03d}")


@tool(ToolManifest("loudness", "Measure integrated loudness, LRA and true peak of a file.", "audio",
                   inputs=["any media file"], outputs=["stdout"], tests=["tests/test_audio.py"]))
def loudness(path: Annotated[Path, typer.Argument(help="Media file (a render, a source...).")], as_json: JsonOpt = False) -> None:
    """EBU R128 measurement (FFmpeg ebur128): integrated LUFS, loudness range, true peak."""
    from cutroom.perception.audio import measure_loudness

    m = measure_loudness(path)
    res = {"integrated_lufs": m.integrated_lufs, "lra_lu": m.lra_lu, "true_peak_dbtp": m.true_peak_dbtp}
    emit(as_json, res)
    if not as_json:
        out.print(f"{path}: {m.integrated_lufs} LUFS, LRA {m.lra_lu} LU, true peak {m.true_peak_dbtp} dBTP")
