"""Analysis commands: analyze (everything + summary.md), frames (on-demand evidence), topics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from cutroom.cli_common import JobArg, JsonOpt, SourcesOpt, emit, err, job_command, out, progress_bar
from cutroom.errors import JobError
from cutroom.job import Job, atomic_write_text
from cutroom.paths import rel_or_abs
from cutroom.perception import pipeline as P
from cutroom.perception import topics as T
from cutroom.tools.registry import ToolManifest, tool

TESTS = ["tests/test_analysis.py"]


def _tc(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def write_summary(job: Job, per_source: dict) -> Path:
    """analysis/summary.md: the one page an agent reads before planning the edit."""
    lines = [f"# Analysis summary: {job.id}", "",
             "Read this first. Details: `analysis/<source>/` (transcript.txt, words.json, audio_report.json,",
             "visual_report.json, pacing.json, sheets/*.jpg). Open the contact sheets to see the footage.", ""]
    for sid, d in per_source.items():
        s = job.source(sid)
        lines += [f"## {sid}: `{s.name}`", "", f"- Media: {s.media.summary()}"]
        v = d.get("visual") or {}
        if v:
            ft = v["footage_type"]
            ev = ", ".join(f"{k} {val}" for k, val in ft["evidence"].items())
            lines += [f"- Footage type (guess, confirm by looking): **{ft['type']}** (confidence {ft['confidence']}; {ev})",
                      f"- Shots: {v['shot_count']}; overview sheet: `{v['overview_sheet']}`"]
            for k, sh in enumerate(v["shots"][:12], 1):
                fr = sh.get("framing", {})
                lines.append(f"  - shot {k} {_tc(sh['start'])}–{_tc(sh['end'])}: faces {sh.get('face_presence', 0):.0%}"
                             + (f", face height {fr.get('face_height')}, centre x {fr.get('face_center_x')}" if fr else "")
                             + (f"; flags: {'; '.join(sh['flags'])}" if sh.get("flags") else "")
                             + (f"; sheet `{sh['sheet']}`" if sh.get("sheet") else ""))
        a = d.get("audio") or {}
        if a:
            lines += [f"- Audio: {a['integrated_lufs']} LUFS integrated, true peak {a['true_peak_dbtp']} dBTP, "
                      f"LRA {a['loudness_range_lu']} LU, noise floor {a['noise_floor_dbfs']} dBFS, "
                      f"speech/noise {a['snr_db']} dB, clipping runs {a['clipping']['clipped_runs']}"]
            for f in a.get("flags", []):
                lines.append(f"  - ⚠ {f}")
        w = d.get("words")
        if w:
            st = w.stats
            low = sum(1 for x in w.words if x.prob < 0.5)
            lines += [f"- Speech: {st['words']} words, {low} low-confidence (check names/numbers), "
                      f"{st['transcribed_fillers']} transcribed fillers, {st['untranscribed_voiced']} untranscribed "
                      f"voiced sounds in pauses (likely um/uh), {st['untranscribed_unvoiced']} breaths/noises; "
                      f"word timing refined at {st['edges_refined']} pause edges"]
        pc = d.get("pacing") or {}
        if pc:
            lines.append(f"- Pacing: {pc['words_per_minute']} words/min, speech {pc['speech_coverage']:.0%} of the time")
            for x in pc["slowest_stretches"][:3]:
                lines.append(f"  - slow/flat stretch {_tc(x['start'])}–{_tc(x['end'])} (score {x['boring']}): "
                             f"\"{x['text']}\"")
        lines.append("")
    lines += ["## Next steps", "",
              f"1. Read `analysis/<source>/transcript.txt` and the contact sheets.",
              f"2. Do the topic map: `analysis/topics.task.md` → `analysis/topics.json` → `cutroom topics {job.id} --check`.",
              f"3. Rough cut: `cutroom rough-cut {job.id}`, then read `cutroom show {job.id}`.", ""]
    path = job.root / "analysis" / "summary.md"
    atomic_write_text(path, "\n".join(lines))
    return path


@tool(ToolManifest("analyze", "Run all perception on a job and write analysis/summary.md.", "analysis",
                   inputs=["ingested sources"],
                   outputs=["analysis/<src>/{words,audio_report,visual_report,pacing,faces}.json", "sheets/*.jpg",
                            "analysis/summary.md", "analysis/topics.task.md"], tests=TESTS))
def analyze(job_id: JobArg, sources: SourcesOpt = None,
            visual: Annotated[bool, typer.Option("--visual/--no-visual", help="Include shot/face/exposure analysis.")] = True,
            force: Annotated[bool, typer.Option(help="Recompute even if results exist.")] = False,
            as_json: JsonOpt = False) -> None:
    """Everything the agent needs to "see and hear" the footage: proxy and audio, transcript,
    refined words (fillers, untranscribed sounds), audio report (loudness, noise, clipping,
    silences), visual report (shots, faces, framing, exposure, blur, contact sheets), pacing map,
    footage-type guess, and a topic-map task. Each step is cached; re-running is cheap."""
    job = Job.load(job_id)
    per: dict[str, dict] = {}
    with job_command(job, "analyze") as summary:
        job.log.section("analyze")
        for s in job.select_sources(sources):
            d: dict = {}
            if s.media.kind == "video":
                if not as_json:
                    err.print(f"[bold]{s.id}[/] proxy")
                P.ensure_proxy(job, s)
            if s.media.primary_audio:
                with progress_bar(f"{s.id} transcribe + words", not as_json) as cb:
                    d["words"] = P.ensure_words(job, s, force=force, on_progress=cb)
                if not as_json:
                    err.print(f"[bold]{s.id}[/] audio report, pacing")
                d["audio"] = P.audio_report(job, s, force=force)
                d["pacing"] = P.pacing(job, s, force=force)
            if visual and s.media.kind == "video":
                if not as_json:
                    err.print(f"[bold]{s.id}[/] shots, faces, contact sheets")
                cov = d["pacing"]["speech_coverage"] if d.get("pacing") else 0.0
                d["visual"] = P.visual_report(job, s, force=force, speech_coverage=cov)
            per[s.id] = d
            if d.get("visual"):
                ft = d["visual"]["footage_type"]
                job.log.note(f"{s.id}: footage type guess **{ft['type']}** ({ft['confidence']}), "
                             f"{d['visual']['shot_count']} shots, flags: {', '.join(d['visual']['flags']) or 'none'}")
            if d.get("audio"):
                a = d["audio"]
                job.log.note(f"{s.id}: {a['integrated_lufs']} LUFS, peak {a['true_peak_dbtp']} dBTP, "
                             f"SNR {a['snr_db']} dB; {'; '.join(a['flags']) or 'no audio flags'}")
        words = {sid: d["words"] for sid, d in per.items() if d.get("words")}
        task = T.prepare(words, job.root / "analysis" / "topics.task.md") if words else None
        path = write_summary(job, per)
        summary["summary"] = rel_or_abs(path, job.root)
    job.mark_stage("analyze", "done")
    result = {"summary": rel_or_abs(path, job.root), "topics_task": rel_or_abs(task, job.root) if task else None,
              "sources": {sid: {"footage_type": (d.get("visual") or {}).get("footage_type"),
                                "audio_flags": (d.get("audio") or {}).get("flags"),
                                "visual_flags": (d.get("visual") or {}).get("flags")} for sid, d in per.items()}}
    emit(as_json, result)
    if not as_json:
        out.print(f"Wrote {result['summary']} (read it first) and {result['topics_task']}.")


@tool(ToolManifest("frames", "Labelled frame grid at chosen times: visual evidence on demand.", "analysis",
                   inputs=["proxy", "timeline (for --timeline times)"], outputs=["JPEG contact sheet"], tests=TESTS))
def frames(job_id: JobArg,
           at: Annotated[list[float], typer.Option("--at", help="Times in seconds (repeat or pass several).")],
           source: Annotated[Optional[str], typer.Option(help="Source id: times are source times. Omit for timeline (HEAD) times.")] = None,
           version: Annotated[Optional[str], typer.Option("--version", "-v", help="Timeline version for timeline times.")] = None,
           grid: Annotated[str, typer.Option(help="Columns x rows, e.g. 3x3 (rows grow if needed).")] = "3x3",
           out_path: Annotated[Optional[Path], typer.Option("--out", help="Output JPEG (default review/frames/...).")] = None,
           as_json: JsonOpt = False) -> None:
    """Grab frames at specific times and tile them, each labelled with its time, into one image
    to open and look at (e.g. around a cut, a graphic entrance, or a suspected glitch)."""
    from cutroom.perception import visual as V
    from cutroom.timeline import store

    job = Job.load(job_id)
    cols = int(grid.lower().split("x")[0])
    picks: list[tuple[str, float, str]] = []
    if source:
        picks = [(source, t, f"{source} {_tc(t)}") for t in at]
    else:
        tl = store.load(job, version)
        for t in at:
            clip = next((c for c in tl.track_clips() if c.start - 1e-6 <= t < c.end), None)
            if clip is None:
                raise JobError(f"{t}s is outside the timeline (0–{tl.duration:.2f}s).", fix="Check the time.")
            picks.append((clip.source, clip.to_source(t), f"v{tl.meta.version:03d} {_tc(t)} ({clip.id})"))
    imgs, labels = [], []
    for sid, st, label in picks:
        s = job.source(sid)
        proxy = P.ensure_proxy(job, s)
        imgs += V.grab_frames(proxy, [st])
        labels.append(label)
    stamp = "_".join(f"{t:g}" for t in at[:6])
    out_file = out_path or job.root / "review" / "frames" / f"frames_{source or 'tl'}_{stamp}.jpg"
    V.contact_sheet(imgs, labels, out_file, cols=cols)
    res = {"path": str(out_file), "frames": len(imgs)}
    emit(as_json, res)
    if not as_json:
        out.print(f"Open {out_file}")


@tool(ToolManifest("topics", "Prepare or check the topic-map task (agent-in-the-loop).", "analysis",
                   inputs=["words.json"], outputs=["analysis/topics.task.md", "analysis/topics.json (by the agent)"],
                   tests=TESTS))
def topics(job_id: JobArg,
           check: Annotated[bool, typer.Option("--check", help="Validate analysis/topics.json instead of preparing the task.")] = False,
           as_json: JsonOpt = False) -> None:
    """Topic segmentation needs judgement, so the agent does it: this writes the task file with
    numbered sentences and the schema; the agent writes analysis/topics.json; --check validates
    that every sentence is covered exactly once and the schema is right."""
    job = Job.load(job_id)
    words = {s.id: P.ensure_words(job, s) for s in job.manifest.sources if s.media.primary_audio}
    if check:
        problems = T.check(job.root / "analysis" / "topics.json", words)
        emit(as_json, {"valid": not problems, "problems": problems})
        if not as_json:
            out.print("[green]topics.json is valid[/]" if not problems else "\n".join(f"- {p}" for p in problems))
        if problems:
            raise typer.Exit(1)
        job.log.note("Topic map checked and valid.")
        return
    path = T.prepare(words, job.root / "analysis" / "topics.task.md")
    emit(as_json, {"task": rel_or_abs(path, job.root)})
    if not as_json:
        out.print(f"Task written: {path}")


@tool(ToolManifest("fix-words", "Correct misheard words (names, terms) in the transcript, keeping their timing.",
                   "analysis", inputs=["analysis/<src>/words.json"], outputs=["words.json", "corrections.json"],
                   tests=["tests/test_analysis.py"]))
def fix_words(job_id: JobArg, find: Annotated[str, typer.Option(help="Words as transcribed, e.g. 'white sand'.")],
              replace: Annotated[str, typer.Option(help="Correct words, e.g. 'White Sands'.")],
              source: Annotated[Optional[str], typer.Option(help="Source id (default: the only one).")] = None,
              at: Annotated[Optional[float], typer.Option(help="Only the occurrence near this source time (s).")] = None,
              as_json: JsonOpt = False) -> None:
    """Fix a name or term the speech model got wrong (found in transcript review, or a
    ⟨?low-confidence?⟩ word). Every occurrence (or the one near --at) is replaced over the same
    time span, recorded in corrections.json (re-applied if words are rebuilt) and copied to
    Shorts derived from this job. Re-plan captions afterwards (`cutroom captions`)."""
    from cutroom.perception.corrections import apply_corrections, corrections_path, load_corrections
    from cutroom.perception.words import load_words

    job = Job.load(job_id)
    if source is None:
        if len(job.manifest.sources) != 1:
            raise JobError("This job has several sources.", fix="Pass --source S0n.")
        source = job.manifest.sources[0].id
    item = {"find": find, "replace": replace, "at": at}
    targets = [job] + [j for j in _derived_jobs(job)]
    counts = {}
    for j in targets:
        adir = j.root / "analysis" / source
        p = adir / "words.json"
        if not p.is_file():
            continue
        wf = load_words(p)
        n = apply_corrections(wf, [item])
        if n:
            atomic_write_text(p, wf.model_dump_json(indent=1, by_alias=True) + "\n")
        items = [i for i in load_corrections(adir) if not (i["find"] == find and i.get("at") == at)] + [item]
        atomic_write_text(corrections_path(adir), json.dumps(items, indent=1) + "\n")
        counts[j.id] = n
        j.log.note(f"fix-words {source}: '{find}' → '{replace}'" + (f" near {at}s" if at is not None else "")
                   + f" ({n} occurrence(s))")
    if not counts.get(job.id):
        raise JobError(f"'{find}' was not found in {source}" + (f" near {at}s" if at is not None else "") + ".",
                       fix="Use the words exactly as transcribed (see analysis/<src>/transcript.txt).")
    emit(as_json, {"replaced": counts})
    if not as_json:
        out.print(f"'{find}' → '{replace}': " + ", ".join(f"{k}: {v}" for k, v in counts.items())
                  + ". Re-plan captions where they exist.")


def _derived_jobs(job: Job) -> list[Job]:
    out_ = []
    for p in sorted(job.root.parent.glob(f"{job.id}.short-*")):
        if (p / "job.json").is_file():
            j = Job.load(p)
            if j.manifest.parent == job.id:
                out_.append(j)
    return out_


@tool(ToolManifest("analyze-reference", "Learn pacing from a reference video into styles/<name>.style.yaml.",
                   "analysis", inputs=["a reference video (references/)"],
                   outputs=["jobs/ref-<name> (analysis)", "styles/<name>.style.yaml"], tests=["tests/test_media.py"]))
def analyze_reference(path: Annotated[Path, typer.Argument(help="Reference video (e.g. references/clip.mp4).")],
                      name: Annotated[str, typer.Option(help="Style name to write.")],
                      force: Annotated[bool, typer.Option(help="Replace an existing style of that name.")] = False,
                      as_json: JsonOpt = False) -> None:
    """Measures the reference (referenced in place, never copied or uploaded): shot lengths and
    cuts per minute, speech rate, the pauses its editor kept between words and sentences, and
    loudness; writes a style that inherits the defaults and overrides pauses and pacing, with
    the measurements in its header. Use it with `cutroom assemble --style <name>`."""
    from cutroom.cli_common import silenced
    from cutroom.ingest.ingest import ingest
    from cutroom.reference import measure, write_style

    if not path.is_file():
        raise JobError(f"No file {path}.", fix="Put the reference video in references/.")
    jid = f"ref-{name}"
    try:
        job = Job.load(jid)
    except JobError:
        job = Job.create(jid, title=f"Reference: {path.name}")
        ingest(job, [path], copy=False)
    with silenced():
        analyze(jid)
    m = measure(Job.load(jid))
    p = write_style(name, path.name, m, force=force)
    emit(as_json, {"style": str(p), "measured": m})
    if not as_json:
        out.print(f"{p.name}: {m['cuts_per_minute']} cuts/min, median shot {m['shot_s']['median']} s, "
                  f"{m['words_per_minute']} words/min, pauses mid {m['pause_mid_s']['median']} s / "
                  f"sentence {m['pause_sentence_s']['median']} s, {m['loudness_lufs']} LUFS")
