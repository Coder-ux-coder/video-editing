"""One review round (render → checks → frames → report) and the autonomous fix loop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from cutroom.job import Job, atomic_write_text
from cutroom.review.checks import CATEGORIES, SEVERITIES, Issue, ReviewInput, run_checks, scorecard
from cutroom.timeline import store
from cutroom.timeline.model import timeline_warnings
from cutroom.timeline.outline import tc

CANNOT_JUDGE = [
    "Whether the story is the right one for the audience (topic choice, emphasis, what was left out).",
    "Taste: whether a graphic, a transition or the music suits the client's brand beyond the style tokens.",
    "Factual correctness of what the speaker says (graphics only show what was said or documented).",
    "Lip sync on footage with a separately recorded audio track (not measured yet).",
    "Sensitive content: names, minors, private information on screen. Read the transcript and look.",
]


def _words(job: Job, tl) -> dict:
    from cutroom.perception.pipeline import ensure_words
    from cutroom.timeline.ops import words_for

    out = {}
    for s in tl.sources:
        out[s.id] = ensure_words(job, job.source(s.id)) if s.has_audio else words_for(job, s.id)
    return out


def _shots(job: Job, tl) -> dict:
    out = {}
    for s in tl.sources:
        p = job.root / "analysis" / s.id / "visual_report.json"
        out[s.id] = ([sh["start"] for sh in json.loads(p.read_text(encoding="utf-8")).get("shots", [])[1:]]
                     if p.is_file() else [])
    return out


def existing_render(job: Job, version: int, mode: str = "preview") -> tuple[dict, Path] | None:
    """A full render of this exact version (versions are immutable, so it can be reused)."""
    js = job.root / "renders" / f"v{version:03d}_{mode}.json"
    mp4 = js.with_suffix(".mp4")
    if js.is_file() and mp4.is_file():
        rep = json.loads(js.read_text(encoding="utf-8"))
        if rep.get("version") == version and not rep.get("range"):
            return rep, mp4
    return None


def review_once(job: Job, version: int | None = None, *, render: bool = True, fresh: bool = False,
                sheets: bool = True, on_progress: Callable[[float], None] | None = None,
                graphics_concurrency: int | None = None) -> dict:
    """Review one version: render (or reuse its render), run every check, sample frames into
    contact sheets, write review/vNNN/report.md and review.json. Returns the review dict."""
    from cutroom.render.engine import plan_segments, render as do_render, render_audio
    from cutroom.style import timeline_style

    n = store.resolve(job, version)
    tl = store.load(job, n)
    words = _words(job, tl)
    style = timeline_style(tl)
    out_dir = job.root / "review" / f"v{n:03d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rep, video = None, None
    if render:
        found = None if fresh else existing_render(job, n)
        if found:
            rep, video = found
        else:
            rep = do_render(job, tl, mode="preview", graphics_concurrency=graphics_concurrency, on_progress=on_progress)
            video = job.root / rep["path"]
    wav = out_dir / "edit_audio.wav"
    render_audio(job, tl, plan_segments(tl), wav)       # unprocessed edit audio, for level checks
    topics_p = job.root / "analysis" / "topics.json"
    ri = ReviewInput(tl=tl, words=words, style=style, job_root=job.root, render=rep, video=video, wav=wav,
                     shots=_shots(job, tl), topics=json.loads(topics_p.read_text(encoding="utf-8")) if topics_p.is_file() else None)
    issues = run_checks(ri)
    accepted = load_accepted(job)
    kept, waived = [], []
    for i in issues:
        a = next((a for a in accepted if a["check"] == i.check and (a.get("at") is None or i.at is None
                                                                     or abs(a["at"] - i.at) <= 0.5)), None)
        (waived if a else kept).append((i, a))
    issues = [i for i, _ in kept]
    score = scorecard(issues)
    sheet_paths = make_sheets(job, tl, video, issues, out_dir) if sheets and video else []
    review = {"version": n, "render": rep["path"] if rep else None, "scorecard": score,
              "issues": [i.as_dict() for i in issues], "warnings": timeline_warnings(tl),
              "accepted": [{**i.as_dict(), "accepted_because": a["reason"]} for i, a in waived],
              "sheets": [str(p.relative_to(job.root)) for p in sheet_paths]}
    atomic_write_text(out_dir / "review.json", json.dumps(review, indent=1) + "\n")
    atomic_write_text(out_dir / "report.md", report_md(job, review, issues))
    wav.unlink(missing_ok=True)
    return review


def load_accepted(job: Job) -> list[dict]:
    p = job.root / "review" / "accepted.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []


def accept_issue(job: Job, check: str, at: float | None, reason: str) -> dict:
    """The editor judged an issue acceptable (or not fixable without a worse change): record it
    with the reason. Accepted issues are listed in every report, never silently dropped."""
    items = load_accepted(job)
    entry = {"check": check, "at": at, "reason": reason}
    items = [i for i in items if not (i["check"] == check and i.get("at") == at)] + [entry]
    (job.root / "review").mkdir(exist_ok=True)
    atomic_write_text(job.root / "review" / "accepted.json", json.dumps(items, indent=1) + "\n")
    job.log.note(f"review: accepted {check}" + (f" at {at:.2f}s" if at is not None else "") + f": {reason}")
    return entry


def make_sheets(job: Job, tl, video: Path, issues: list[Issue], out_dir: Path, every: float = 4.0) -> list[Path]:
    """Dense frame sampling of the render: one sheet per minute (a frame every 4 s) plus one sheet
    with a frame at every issue, labelled with time and issue. The agent opens these and judges."""
    from cutroom.perception import visual as V

    paths = []
    dur = tl.duration
    times = [round(t, 2) for t in [x * every for x in range(int(dur // every) + 1)] if t < dur - 0.05]
    per = 15
    for k in range(0, len(times), per):
        chunk = times[k:k + per]
        frames = V.grab_frames(video, chunk, 216)
        p = out_dir / f"sheet_{k // per + 1:02d}.jpg"
        V.contact_sheet(frames, [tc(t, ms=False) for t in chunk], p, cols=5)
        paths.append(p)
    flagged = [i for i in issues if i.at is not None and i.severity != "polish"][:20]
    if flagged:
        ts = [min(max(0.0, i.at + 0.2), dur - 0.05) for i in flagged]
        frames = V.grab_frames(video, ts, 216)
        p = out_dir / "issues.jpg"
        V.contact_sheet(frames, [f"{tc(t, ms=False)} {i.check}" for t, i in zip(ts, flagged)], p, cols=4)
        paths.append(p)
    return paths


def report_md(job: Job, review: dict, issues: list[Issue]) -> str:
    sc = review["scorecard"]
    lines = [f"# Review: {job.id} v{review['version']:03d}", "",
             f"Render: `{review['render']}`" if review["render"] else "Render: (not rendered)", "",
             f"**{'Ready for the human gate' if sc['ready'] else 'Not ready'}**: "
             f"{sc['counts']['blocker']} blocker, {sc['counts']['major']} major, {sc['counts']['minor']} minor, "
             f"{sc['counts']['polish']} polish. Overall {sc['overall']}/10.", "",
             "| " + " | ".join(CATEGORIES) + " |", "|" + "---|" * len(CATEGORIES),
             "| " + " | ".join(f"{sc['categories'][c]}" for c in CATEGORIES) + " |", ""]
    for sev in SEVERITIES:
        group = [i for i in issues if i.severity == sev]
        if not group:
            continue
        lines += [f"## {sev.capitalize()} ({len(group)})", ""]
        for i in group:
            at = f" at {tc(i.at)}" if i.at is not None else ""
            lines.append(f"- **{i.title}**{at} [{i.check}]  ")
            lines.append(f"  {i.evidence}  ")
            if i.fix:
                lines.append(f"  Fix: `{i.fix.replace('<job>', job.id)}`" + (" (automatic)" if i.auto else ""))
        lines.append("")
    if review.get("accepted"):
        lines += ["## Accepted by the editor", ""] + [
            f"- **{a['title']}**" + (f" at {tc(a['at'])}" if a.get("at") is not None else "") +
            f" [{a['check']}]: {a['accepted_because']}" for a in review["accepted"]] + [""]
    if review.get("warnings"):
        lines += ["## Timeline warnings", ""] + [f"- {w}" for w in review["warnings"]] + [""]
    if review.get("sheets"):
        lines += ["## Look at these frames", "", "Open each sheet; judge framing, graphics, captions and "
                  "continuity by eye (the checks cannot see taste).", ""]
        lines += [f"- `{s}`" for s in review["sheets"]] + [""]
    lines += ["## Not judged automatically", ""] + [f"- {x}" for x in CANNOT_JUDGE] + [""]
    return "\n".join(lines)


def review_loop(job: Job, *, rounds: int = 4, fix: bool = True, log: Callable[[str], None] = lambda s: None,
                graphics_concurrency: int | None = None) -> dict:
    """Review → apply automatic fixes → review again, until no blocker/major remains that the
    loop can fix, or ``rounds`` reviews have run. Stops and reports; never ships."""
    from cutroom.review.fixes import apply_fixes

    history = []
    for r in range(1, rounds + 1):
        rev = review_once(job, graphics_concurrency=graphics_concurrency)
        sc = rev["scorecard"]
        log(f"round {r}: v{rev['version']:03d} {sc['counts']} overall {sc['overall']}")
        entry = {"round": r, "version": rev["version"], "scorecard": sc, "fixes": []}
        history.append(entry)
        issues = [Issue(**i) for i in rev["issues"]]
        fixable = [i for i in issues if i.auto and i.severity in ("blocker", "major", "minor")]
        if not fix or not fixable or r == rounds:
            break
        entry["fixes"] = apply_fixes(job, issues, log)
        if not any("applied" in f for f in entry["fixes"]):
            break
    final = history[-1]
    out = {"rounds": history, "final_version": final["version"], "ready": final["scorecard"]["ready"],
           "report": f"review/v{final['version']:03d}/report.md"}
    atomic_write_text(job.root / "review" / "loop.json", json.dumps(out, indent=1) + "\n")
    return out
