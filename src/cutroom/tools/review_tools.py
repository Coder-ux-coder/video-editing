"""Review loop command: render → automatic checks → frames → report → (optionally) fix and repeat."""

from __future__ import annotations

from typing import Annotated, Optional

import typer

from cutroom.cli_common import JobArg, JsonOpt, VersionOpt, emit, out, progress_bar
from cutroom.job import Job
from cutroom.tools.registry import ToolManifest, tool

TESTS = ["tests/test_review.py"]


@tool(ToolManifest("review", "Review a version: render, automatic checks, frame sheets, report and scorecard; --fix loops.",
                   "review", inputs=["timeline version", "words.json", "analysis reports", "render"],
                   outputs=["review/vNNN/report.md", "review/vNNN/review.json", "review/vNNN/*.jpg",
                            "timeline versions (with --fix)"], tests=TESTS, changes_timeline=True))
def review(job_id: JobArg, version: VersionOpt = None,
           fix: Annotated[bool, typer.Option("--fix", help="Apply automatic fixes and review again, up to --rounds.")] = False,
           rounds: Annotated[int, typer.Option(help="Maximum review rounds with --fix.")] = 4,
           fresh: Annotated[bool, typer.Option(help="Render again even if this version has a render.")] = False,
           no_render: Annotated[bool, typer.Option("--no-render", help="Checks that need no render only (fast).")] = False,
           accept: Annotated[Optional[str], typer.Option(help="Accept an issue after judgement: CHECK or CHECK@SECONDS.")] = None,
           reason: Annotated[Optional[str], typer.Option(help="Why it is acceptable (required with --accept).")] = None,
           as_json: JsonOpt = False) -> None:
    """Checks audio (loudness, peaks, clicks, pauses and fillers left, level jumps), cuts (mid-word
    cuts, flash shots, undisguised jump cuts, clipped words), captions (missing, stale, safe zone,
    sync), graphics (on faces, outside the safe area, unsourced, detached, too short, crowded),
    video (frame count, black or frozen frames, quality flags) and structure (ending, opening,
    static stretches, low-value topics). Writes review/vNNN/report.md with issues by severity,
    evidence and a fix command, a scorecard, and contact sheets to look at. With --fix, applies
    the automatic fixes and repeats until no blocker or major remains or --rounds is reached;
    delivery still needs the human gate."""
    from cutroom.review.run import review_loop, review_once
    from cutroom.tools.render_tools import graphics_concurrency
    from cutroom.system.profile import load_profile

    job = Job.load(job_id)
    if accept:
        from cutroom.errors import RefusedError
        from cutroom.review.run import accept_issue

        if not reason:
            raise RefusedError("Say why the issue is acceptable.", fix="Add --reason '…' (it goes in every report).")
        check, _, at = accept.partition("@")
        entry = accept_issue(job, check, float(at) if at else None, reason)
        emit(as_json, entry)
        if not as_json:
            out.print(f"Accepted {accept}: {reason}")
        return
    job.log.section("review" + (" --fix" if fix else ""))
    try:
        conc = graphics_concurrency(load_profile())
    except Exception:
        conc = None
    if fix:
        res = review_loop(job, rounds=rounds, log=lambda s: (job.log.note(s), None if as_json else out.print(s)),
                          graphics_concurrency=conc)
        emit(as_json, res)
        if not as_json:
            out.print(f"{'Ready for the human gate' if res['ready'] else 'Not ready'} after {len(res['rounds'])} "
                      f"round(s): v{res['final_version']:03d}; report {res['report']}")
        return
    with progress_bar("review render", not as_json) as prog:
        rev = review_once(job, version, render=not no_render, fresh=fresh, on_progress=prog, graphics_concurrency=conc)
    sc = rev["scorecard"]
    job.log.note(f"review v{rev['version']:03d}: {sc['counts']}, overall {sc['overall']}")
    emit(as_json, rev)
    if not as_json:
        out.print(f"v{rev['version']:03d}: {sc['counts']['blocker']} blocker, {sc['counts']['major']} major, "
                  f"{sc['counts']['minor']} minor, {sc['counts']['polish']} polish; overall {sc['overall']}/10 → "
                  f"review/v{rev['version']:03d}/report.md")
        for i in rev["issues"][:15]:
            out.print(f"  [{i['severity']}] {i['title']}: {i['evidence'][:110]}", markup=False, highlight=False)
