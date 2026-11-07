"""Render commands: render (preview/final) and rough-cut (the standard first pass, end to end)."""

from __future__ import annotations

from typing import Annotated, Literal, Optional

import typer

from cutroom.cli_common import (JobArg, JsonOpt, SourcesOpt, VersionOpt, emit, err, job_command, out,
                                progress_bar, silenced)
from cutroom.job import Job
from cutroom.render.engine import render as do_render
from cutroom.style import timeline_style
from cutroom.system.profile import load_profile
from cutroom.timeline import store
from cutroom.timeline.outline import tc
from cutroom.tools.registry import ToolManifest, tool

TESTS = ["tests/test_render.py"]


def _parse_range(spec: str | None) -> tuple[float, float] | None:
    if not spec:
        return None
    try:
        a, b = (float(x) for x in spec.split("-", 1))
    except ValueError:
        from cutroom.errors import RefusedError

        raise RefusedError(f"Bad range '{spec}'.", fix="Use timeline seconds, e.g. --range 30-55.") from None
    return a, b


def _render(job: Job, version: str | None, mode: str, as_json: bool, *, captions: bool = True,
            audio_chain: bool = True, span: tuple[float, float] | None = None) -> dict:
    tl = store.load(job, version)
    prof = load_profile()
    style = timeline_style(tl)
    enc = prof.render.preview_encoder if mode == "preview" else prof.render.final_encoder
    with job_command(job, "render") as summary, progress_bar(f"render v{tl.meta.version:03d} {mode}", not as_json) as cb:
        rep = do_render(job, tl, mode=mode, encoder=enc, join_fade=style["editing"]["join_fade"],
                        timecode=style["output"]["preview"].get("timecode", True), captions=captions,
                        audio_chain=audio_chain, graphics_concurrency=graphics_concurrency(prof), span=span,
                        on_progress=cb)
        summary.update(rep)
    if rep.get("audio_chain"):
        a = rep["audio_chain"]
        job.log.note(f"voice chain applied ({a['mode']} loudness): {a['before_lufs']} → {a['after_lufs']} LUFS, "
                     f"true peak {a['after_true_peak_dbtp']} dBTP")
    job.log.section(f"render v{tl.meta.version:03d} ({mode})")
    job.log.note(f"`{rep['path']}` {rep['size']} {tc(rep['duration_s'])}, {rep['clips']} clips, {rep['joins']} joins, "
                 f"clicks: {len(rep['clicks'])}, {rep['elapsed_s']}s. Checks: "
                 + ", ".join(f"{k} {'OK' if v else 'FAILED'}" for k, v in rep["checks"].items()))
    for c in rep["clicks"]:
        job.log.warn(f"possible click at {c['time']}s (jump {c['jump']})")
    if span is None:
        job.mark_stage(f"render_{mode}", "done", version=tl.meta.version, path=rep["path"])
    return rep


def graphics_concurrency(prof) -> int:
    """Chrome tabs for Remotion: one per physical core, ~0.6 GB each within the memory budget."""
    cores = prof.machine.get("cpu", {}).get("physical_cores") or 2
    return max(1, min(cores, int(prof.memory_budget_gb / 0.6), 8))


def _print_report(rep: dict) -> None:
    ok = all(rep["checks"].values())
    out.print(f"{'[green]OK[/]' if ok else '[red]CHECK FAILED[/]'} {rep['path']}: {rep['size']} @ {rep['fps']} fps, "
              f"{tc(rep['duration_s'])} (expected {tc(rep['expected_duration_s'])}), {rep['clips']} clips, "
              f"{rep['joins']} joins, clicks {len(rep['clicks'])}, {rep['elapsed_s']}s")
    ld = rep.get("loudness", {})
    out.print(f"   loudness {ld.get('integrated_lufs')} LUFS, true peak {ld.get('true_peak_dbtp')} dBTP"
              + (f"; captions: {rep['captions']['cues']} ({rep['captions']['style']}), sync "
                 f"{rep['captions']['sync']}" if rep.get("captions") else ""))
    for k, v in rep["checks"].items():
        if not v:
            out.print(f"   [red]failed: {k}[/]")


@tool(ToolManifest("render", "Render a timeline version (fast preview or full-quality final).", "render",
                   inputs=["timeline version", "proxies (preview) or sources (final)", "48 kHz audio"],
                   outputs=["renders/vNNN_<mode>.mp4", "renders/vNNN_<mode>.json (checks)"], tests=TESTS))
def render(job_id: JobArg, version: VersionOpt = None,
           mode: Annotated[Literal["preview", "final"], typer.Option(help="preview: 540p from proxies with burned-in timecode; final: full resolution from sources.")] = "preview",
           captions: Annotated[bool, typer.Option("--captions/--no-captions", help="Burn in the timeline's captions (if any).")] = True,
           audio_chain: Annotated[bool, typer.Option("--audio-chain/--raw-audio", help="Apply the timeline's voice chain and loudness (if any).")] = True,
           range_: Annotated[Optional[str], typer.Option("--range", help="Render only timeline seconds START-END, e.g. 30-55 (for reviewing a section).")] = None,
           as_json: JsonOpt = False) -> None:
    """Render a version. Every audio join gets a short fade (no clicks); afterwards the render is
    checked: duration and frame count match the timeline, and every join is scanned for clicks."""
    job = Job.load(job_id)
    rep = _render(job, version, mode, as_json, captions=captions, audio_chain=audio_chain, span=_parse_range(range_))
    emit(as_json, rep)
    if not as_json:
        _print_report(rep)
    if not all(rep["checks"].values()):
        raise typer.Exit(1)


@tool(ToolManifest("rough-cut", "Standard first pass: assemble → silence → fillers → retakes → preview.", "cutting",
                   inputs=["ingested sources"], outputs=["timeline versions (one per step)", "preview render"],
                   tests=["tests/test_cutting.py", "tests/test_render.py"], changes_timeline=True))
def rough_cut(job_id: JobArg, sources: SourcesOpt = None,
              style: Annotated[Optional[str], typer.Option(help="Style name (default: the timeline's or 'default').")] = None,
              retake_confidence: Annotated[float, typer.Option(help="Apply retake candidates at or above this confidence.")] = 0.75,
              fresh: Annotated[bool, typer.Option(help="Start from a new assembly even if a timeline exists.")] = False,
              render_preview: Annotated[bool, typer.Option("--render/--no-render", help="Render a preview at the end.")] = True,
              as_json: JsonOpt = False) -> None:
    """Run the standard rough cut. Each step saves its own version, so any step can be inspected
    (`cutroom diff`), undone or adjusted. Prints the resulting outline path and render checks."""
    from cutroom.tools.cutting import detect_retakes, remove_fillers, remove_silence
    from cutroom.tools.timeline_tools import assemble

    job = Job.load(job_id)
    quiet = dict(as_json=True) if as_json else {}
    steps = []
    if fresh or store.head(job) is None:
        err.print("[bold]1/5 assemble[/]") if not as_json else None
        with silenced(as_json):
            assemble(job_id, sources, style=style or "default", **quiet)
        steps.append("assemble")
    for i, (name, fn, kw) in enumerate([("remove-silence", remove_silence, {}),
                                        ("remove-fillers", remove_fillers, {}),
                                        ("detect-retakes", detect_retakes,
                                         {"apply": True, "min_confidence": retake_confidence})], 2):
        if not as_json:
            err.print(f"[bold]{i}/5 {name}[/]")
        with silenced(as_json):
            fn(job_id, None, **kw, **quiet)
        steps.append(name)
    rep = None
    if render_preview:
        if not as_json:
            err.print("[bold]5/5 render preview[/]")
        rep = _render(job, None, "preview", as_json)
        if not as_json:
            _print_report(rep)
    tl = store.load(job)
    result = {"version": tl.meta.version, "steps": steps, "duration_s": round(tl.duration, 3),
              "source_duration_s": round(sum(s.duration for s in tl.sources), 3), "render": rep}
    emit(as_json, result)
    if not as_json:
        out.print(f"Rough cut v{tl.meta.version:03d}: {tc(result['source_duration_s'])} → {tc(tl.duration)}. "
                  f"Read it with `cutroom show {job.id}`; see what each step did with `cutroom versions {job.id}`.")
