"""Delivery commands: export (NLE), publish (title/description/chapters), revise (client notes),
deliver (package, awaiting approval), approve (the human gate), next (where the job stands)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from cutroom.cli_common import JobArg, JsonOpt, VersionOpt, emit, out
from cutroom.errors import RefusedError
from cutroom.job import Job
from cutroom.timeline import store
from cutroom.tools.registry import ToolManifest, tool

TESTS = ["tests/test_delivery.py"]


@tool(ToolManifest("export", "Export the edit for an NLE: OTIO (Resolve), FCPXML (Resolve/FCP), EDL (Premiere/Avid).",
                   "delivery", inputs=["timeline version"], outputs=["renders/nle/*.otio|.fcpxml|.edl|.srt"], tests=TESTS))
def export(job_id: JobArg, formats: Annotated[str, typer.Option("--formats", help="Comma list: otio,fcpxml,edl.")] = "otio,fcpxml,edl",
           version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Cuts on the renderer's frame grid; b-roll on V2, music and effects on A2/A3 (OTIO, FCPXML);
    chapters and graphics as markers; captions as SRT. Each file is read back and compared with the
    timeline before it is reported."""
    from cutroom.export.nle import export_all

    job = Job.load(job_id)
    tl = store.load(job, version)
    res = export_all(job, tl, job.root / "renders" / "nle", tuple(f.strip() for f in formats.split(",")))
    emit(as_json, res)
    if not as_json:
        for k, v in res.items():
            p = v if isinstance(v, str) else v["path"]
            extra = "" if isinstance(v, str) else f" ({v.get('clips', v.get('events'))} clips/events, {v['frames']} frames, verified)"
            out.print(f"{k}: {Path(p).relative_to(job.root)}{extra}")


@tool(ToolManifest("publish", "Title, description, chapters and tags for the long-form video: task and check.",
                   "delivery", inputs=["timeline HEAD", "topics.json", "brief.md"],
                   outputs=["publish.task.md", "delivery/description.txt", "timeline vN+1 (chapter markers)"],
                   tests=TESTS, changes_timeline=True))
def publish(job_id: JobArg, check: Annotated[bool, typer.Option("--check", help="Validate publish.json and apply it.")] = False,
            as_json: JsonOpt = False) -> None:
    """Writes publish.task.md (chapters proposed from the topic map, credits collected); with
    --check validates publish.json (title, numbers said, YouTube chapter rules, limits), writes the
    chapters into the timeline and delivery/description.txt."""
    from cutroom import publish as P

    job = Job.load(job_id)
    if not check:
        p = P.write_task(job)
        emit(as_json, {"task": str(p.relative_to(job.root))})
        if not as_json:
            out.print(f"Wrote {p.relative_to(job.root)}; write publish.json, then `cutroom publish {job.id} --check`.")
        return
    res = P.check(job)
    n = P.apply(job, res) if res["ok"] else None
    emit(as_json, {**res, "version": n})
    if not as_json:
        out.print("[green]publish.json OK[/]" + (f" → chapters in v{n:03d}" if n else "") if res["ok"]
                  else "[red]publish.json has problems:[/]")
        for p in res["problems"]:
            out.print(f"  - {p}")
    if not res["ok"]:
        raise typer.Exit(1)


@tool(ToolManifest("revise", "Client notes → a traceable revision round (prepare, then --apply the plan).",
                   "delivery", inputs=["client notes", "the version they watched"],
                   outputs=["review/revisions/rNN/{task.md,plan.json,result.json,report.md}", "timeline versions"],
                   tests=TESTS, changes_timeline=True))
def revise(job_id: JobArg,
           notes: Annotated[Optional[Path], typer.Option(help="Text file with the client's notes (one per line).")] = None,
           seen: Annotated[Optional[str], typer.Option(help="Version the client watched (times are mapped from it).")] = None,
           apply_: Annotated[Optional[str], typer.Option("--apply", help="Apply a filled plan, e.g. r01.")] = None,
           as_json: JsonOpt = False) -> None:
    """Prepare: maps each note's timecodes from the version the client watched to the current edit
    and shows what is said and on screen there; the agent writes commands or a response per note in
    plan.json. --apply runs them (each a version) and writes a note-by-note report."""
    from cutroom import revise as R

    job = Job.load(job_id)
    if apply_:
        res = R.apply(job, apply_)
        emit(as_json, res)
        if not as_json:
            out.print(f"Revision {apply_}: v{res['base_version']:03d} → v{res['final_version']:03d}; "
                      f"report review/revisions/{apply_}/report.md")
            for r in res["notes"]:
                out.print(f"  note {r['n']}: {'ok' if r['ok'] else 'FAILED'} ({len(r['done'])} command(s))")
        return
    if notes is None:
        raise RefusedError("Give the notes.", fix="--notes notes.txt [--seen vNNN], or --apply rNN.")
    rd = R.prepare(job, notes.read_text(encoding="utf-8"), store.resolve(job, seen) if seen else None)
    emit(as_json, {"round": rd.name, "task": str((rd / "task.md").relative_to(job.root))})
    if not as_json:
        out.print(f"Revision {rd.name}: read {(rd / 'task.md').relative_to(job.root)}, fill plan.json, then "
                  f"`cutroom revise {job.id} --apply {rd.name}`.")


@tool(ToolManifest("deliver", "Assemble the delivery package for a version (marked AWAITING APPROVAL).", "delivery",
                   inputs=["final render", "review", "publish.json", "thumbnail", "Shorts"],
                   outputs=["delivery/<job>_vNNN/ (video, captions, thumbnail, texts, Shorts, NLE files, report, checksums)"],
                   tests=TESTS))
def deliver(job_id: JobArg, version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Refuses unless everything is ready (final render checks, review without blockers/majors,
    publishing text, thumbnail, every Short). The package waits for a person's approval."""
    from cutroom.deliver import prepare

    job = Job.load(job_id)
    pkg = prepare(job, store.resolve(job, version) if version else None)
    emit(as_json, {"package": str(pkg.relative_to(job.root)), "status": "AWAITING APPROVAL"})
    if not as_json:
        out.print(f"{pkg.relative_to(job.root)}: AWAITING APPROVAL. See {pkg.name}/APPROVAL.md (a person approves).")


@tool(ToolManifest("approve", "Record a person's approval of a delivery package (the human gate).", "delivery",
                   inputs=["delivery package"], outputs=["MANIFEST.json status APPROVED"], tests=TESTS))
def approve(job_id: JobArg, package: Annotated[str, typer.Option(help="Package folder name in delivery/.")],
            by: Annotated[str, typer.Option(help="The approver's name.")],
            notes: Annotated[str, typer.Option(help="Optional notes.")] = "", as_json: JsonOpt = False) -> None:
    """For the person who watched the package. An agent never runs this for its own work."""
    from cutroom.deliver import approve as do_approve

    m = do_approve(Job.load(job_id), package, by, notes)
    emit(as_json, m)
    if not as_json:
        out.print(f"{package}: APPROVED by {by}")


# --------------------------------------------------------------------------- orchestration


def job_state(job: Job) -> list[dict]:
    """Every stage of the playbook with done/not done and the next command."""
    st = []

    def add(stage: str, done: bool, cmd: str, playbook: str, note: str = "") -> None:
        st.append({"stage": stage, "done": bool(done), "next": cmd.replace("<job>", job.id), "playbook": playbook,
                   "note": note})

    srcs = job.manifest.sources
    add("ingest", bool(srcs), "cutroom ingest <job> <footage>", "01_intake.md")
    an = all((job.root / "analysis" / s.id / "words.json").is_file() and
             (job.root / "analysis" / s.id / "visual_report.json").is_file() for s in srcs) and bool(srcs)
    add("analyze", an, "cutroom analyze <job>", "01_intake.md")
    add("topic map", (job.root / "analysis" / "topics.json").is_file(), "cutroom topics <job> (then write topics.json)",
        "03_story_and_cut_plan.md")
    head = store.head(job) if (job.root / "versions").is_dir() and any((job.root / "versions").glob("v*.json")) else None
    tl = store.load(job, head) if head else None
    cmds = [c.command for c in tl.meta.changelog] if tl else []
    add("rough cut", any(c in ("remove-silence", "rough-cut") for c in cmds), "cutroom rough-cut <job>", "04_rough_cut.md")
    add("audio", bool(tl and tl.audio.voice_chain), "cutroom audio <job>", "05_audio.md")
    add("captions", bool(tl and tl.captions and tl.captions.cues), "cutroom captions <job>", "06_graphics_and_captions.md")
    add("graphics", bool(tl and tl.graphics), "cutroom graphic-add <job> lower-third … (or skip if none helps)",
        "06_graphics_and_captions.md", "optional")
    add("b-roll / music", bool(tl and (tl.broll or tl.audio.music)), "cutroom broll-suggest <job>; cutroom music <job> …",
        "07_broll_and_music.md", "optional")
    rv = job.root / "review" / f"v{head:03d}" / "review.json" if head else None
    ready = bool(rv and rv.is_file() and json.loads(rv.read_text(encoding="utf-8"))["scorecard"]["ready"])
    add("review", ready, "cutroom review <job> --fix", "09_review_loop.md")
    if job.manifest.parent is None:
        from cutroom.tools.analysis_tools import _derived_jobs

        add("shorts", bool(_derived_jobs(job)), "cutroom shorts-find <job>", "08_shorts.md", "if the brief asks")
        add("publishing text", (job.root / "delivery" / "description.txt").is_file(), "cutroom publish <job>", "10_delivery.md")
        add("thumbnail", (job.root / "renders" / "thumbnail.jpg").is_file(),
            "cutroom thumbnails <job> --text \"…\"", "07_broll_and_music.md")
    add("final render", bool(head and (job.root / "renders" / f"v{head:03d}_final.json").is_file()),
        "cutroom render <job> --mode final", "10_delivery.md")
    pkgs = sorted((job.root / "delivery").glob(f"{job.id}_v*/MANIFEST.json")) if (job.root / "delivery").is_dir() else []
    status = json.loads(pkgs[-1].read_text(encoding="utf-8"))["status"] if pkgs else None
    add("delivery package", bool(pkgs) and pkgs[-1].parent.name.endswith(f"v{head:03d}"), "cutroom deliver <job>", "10_delivery.md")
    add("approval (a person)", status == "APPROVED", f"a person: cutroom approve <job> --package … --by …", "10_delivery.md",
        "the human gate")
    return st


@tool(ToolManifest("next", "Where a job stands in the playbook, and the next command.", "delivery",
                   inputs=["job folder"], outputs=["stage list"], tests=TESTS))
def next_step(job_id: JobArg, as_json: JsonOpt = False) -> None:
    """Reads the job (sources, analysis, timeline history, reviews, renders, delivery) and lists
    each playbook stage as done or not, with the next command to run."""
    st = job_state(Job.load(job_id))
    nxt = next((s for s in st if not s["done"] and s["note"] not in ("optional", "if the brief asks")), None)
    emit(as_json, {"stages": st, "next": nxt})
    if not as_json:
        for s in st:
            mark = "[green]✓[/]" if s["done"] else ("[dim]·[/]" if s["note"] else "[yellow]○[/]")
            out.print(f" {mark} {s['stage']}" + (f" [dim]({s['note']})[/]" if s["note"] and not s["done"] else ""))
        if nxt:
            out.print(f"Next: [bold]{nxt['next']}[/]  (prompts/{nxt['playbook']})")
        else:
            out.print("Everything is done.")
