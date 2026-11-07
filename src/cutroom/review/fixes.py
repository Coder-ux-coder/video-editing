"""Automatic fixes for review issues marked ``auto``. Each fix applies an existing cutroom
operation, so every change is a normal timeline version with a changelog line.

Order inside one round matters: pauses and fillers first, then flash shots (recomputed on the
edit as it is then, since pause edits move cuts), then framing (punch-ins, which depend on the
clips), then graphics, then captions (which depend on the final words), then audio."""

from __future__ import annotations

from typing import Callable

from cutroom.cli_common import silenced
from cutroom.errors import CutroomError
from cutroom.job import Job
from cutroom.review.checks import Issue
from cutroom.timeline import store

ORDER = ["audio.long_pauses", "audio.fillers", "cuts.flash_shot", "cuts.jump_cuts", "graphics.unsourced",
         "graphics.detached", "graphics.too_short", "graphics.face", "graphics.safe_area", "captions.missing",
         "captions.stale", "audio.no_chain", "audio.loudness", "audio.true_peak"]


def _fix_silence(job: Job, issue: Issue) -> str:
    from cutroom.tools.cutting import remove_silence

    remove_silence(job.id)
    return "remove-silence"


def _fix_fillers(job: Job, issue: Issue) -> str:
    from cutroom.tools.cutting import remove_fillers

    remove_fillers(job.id)
    return "remove-fillers"


def _fix_flash(job: Job, issue: Issue) -> str:
    """Fix every flash shot of the current edit that can be fixed safely: trim a word-free sliver
    so the cut lands on the camera change, else lengthen the shot into the removed pause next to
    it. Recomputed on HEAD (earlier fixes this round may have moved the cuts)."""
    from cutroom.review.checks import ReviewInput, flash_shots
    from cutroom.review.run import _shots
    from cutroom.style import timeline_style
    from cutroom.timeline.ops import Removal, extend_clip_edge, remove_ranges, words_for

    tl = store.load(job).model_copy(deep=True)
    words = {s.id: words_for(job, s.id) for s in tl.sources}
    shots = _shots(job, tl)
    style = timeline_style(tl)
    nxt = (max(store.list_versions(job)) + 1) if store.list_versions(job) else 1
    done, skipped = [], set()
    for _ in range(20):
        todo = [f for f in flash_shots(ReviewInput(tl, words, style, job.root, shots=shots))
                if f["auto"] and (f["clip"], f["where"]) not in skipped]
        if not todo:
            break
        d = todo[0]
        wf = words.get(d["source"])
        try:
            if d["where"] in ("head", "tail") and not d["has_word"] and _trim_ok(d, wf, tl):
                remove_ranges(tl, [Removal(d["source"], d["src_from"], d["src_to"], "manual",
                                           "flash shot: the cut now lands on the camera change")],
                              tool="review-fix", version=nxt, words=words, min_clip=0.05)
                done.append(f"{d['start']:.2f}s trimmed onto the camera change")
            elif d["extendable"]:
                need = 0.42 - d["duration"]
                edge = {"tail": "out", "head": "in", "clip-out": "out", "clip-in": "in"}[d["where"]]
                extend_clip_edge(tl, d["clip"], edge, need, words=words)
                done.append(f"{d['start']:.2f}s lengthened by {need:.2f}s")
            else:
                skipped.add((d["clip"], d["where"]))
        except CutroomError:
            skipped.add((d["clip"], d["where"]))
    if not done:
        raise CutroomError("No flash shot could be fixed safely.", fix="Fix the remaining ones by hand.")
    store.save_new(job, tl, command="review --fix", summary="flash shots: " + "; ".join(done))
    return "flash shots: " + "; ".join(done)


def _trim_ok(d: dict, wf, tl) -> bool:
    """Trimming the sliver must leave the word pads (0.06 s before a word, 0.10 s after)."""
    clip = next(c for c in tl.clips if c.id == d["clip"])
    words = wf.words if wf else []
    if d["where"] == "tail":
        last = max((w.end for w in words if clip.src_in <= w.end <= d["src_from"] + 1e-3), default=None)
        return last is None or d["src_from"] - last >= 0.08
    first = min((w.start for w in words if d["src_to"] - 1e-3 <= w.start <= clip.src_out), default=None)
    return first is None or first - d["src_to"] >= 0.05


def _fix_punch(job: Job, issue: Issue) -> str:
    from cutroom.tools.graphics_tools import punch_in

    punch_in(job.id, auto=True)
    return "punch-in --auto"


def _fix_graphic_remove(job: Job, issue: Issue) -> str:
    from cutroom.tools.graphics_tools import graphic_remove

    graphic_remove(job.id, issue.data["id"])
    return f"graphic-remove {issue.data['id']}"


def _fix_graphic_duration(job: Job, issue: Issue) -> str:
    from cutroom.tools.graphics_tools import graphic_move

    graphic_move(job.id, issue.data["id"], duration=issue.data["duration"])
    return f"graphic-move {issue.data['id']} --duration {issue.data['duration']}"


def _fix_graphic_replace(job: Job, issue: Issue) -> str:
    from cutroom.tools.graphics_tools import graphic_move

    graphic_move(job.id, issue.data["id"], replace=True)
    return f"graphic-move {issue.data['id']} --replace"


def _fix_captions(job: Job, issue: Issue) -> str:
    from cutroom.tools.captions_tools import captions

    style = issue.data.get("style")
    captions(job.id, style=style, output=issue.data.get("output", "youtube_long"),
             burn_in=issue.data.get("burn_in", True))
    return f"captions --style {style}" if style else "captions"


def _fix_audio(job: Job, issue: Issue) -> str:
    from cutroom.tools.captions_tools import audio

    audio(job.id)
    return "audio"


FIXES: dict[str, Callable[[Job, Issue], str]] = {
    "audio.long_pauses": _fix_silence, "audio.fillers": _fix_fillers, "cuts.flash_shot": _fix_flash,
    "cuts.jump_cuts": _fix_punch, "graphics.unsourced": _fix_graphic_remove, "graphics.detached": _fix_graphic_remove,
    "graphics.too_short": _fix_graphic_duration, "graphics.face": _fix_graphic_replace,
    "graphics.safe_area": _fix_graphic_replace, "captions.missing": _fix_captions, "captions.stale": _fix_captions,
    "audio.no_chain": _fix_audio, "audio.loudness": _fix_audio, "audio.true_peak": _fix_audio,
}
ONCE_PER_ROUND = {"audio.long_pauses", "audio.fillers", "cuts.flash_shot", "cuts.jump_cuts", "captions.missing", "captions.stale",
                  "audio.no_chain", "audio.loudness", "audio.true_peak"}


def apply_fixes(job: Job, issues: list[Issue], log: Callable[[str], None] = lambda s: None) -> list[dict]:
    """Apply every auto-fixable blocker/major/minor issue once, in dependency order.
    Returns what was done (and what failed, with the reason)."""
    done: list[dict] = []
    seen: set[str] = set()
    todo = [i for i in issues if i.auto and i.check in FIXES and i.severity != "polish"]
    todo.sort(key=lambda i: ORDER.index(i.check) if i.check in ORDER else 99)
    # Captions depend on every structural edit of this round: re-plan them last if anything changed.
    structural = {"audio.long_pauses", "audio.fillers", "cuts.flash_shot"}
    if any(i.check in structural for i in todo) and not any(i.check in ("captions.stale", "captions.missing") for i in todo):
        caps = store.load(job).captions
        if caps is not None and caps.cues:
            todo.append(Issue("captions.stale", "captions", "major", "Captions re-planned after edits", "",
                              fix="cutroom captions <job>", auto=True,
                              data={"style": caps.style, "output": caps.safe_zone, "burn_in": caps.burn_in}))
    audio_done = False
    for issue in todo:
        key = issue.check if issue.check in ONCE_PER_ROUND else f"{issue.check}:{issue.data.get('id') or issue.at}"
        if issue.check.startswith("audio.") and issue.check in ("audio.no_chain", "audio.loudness", "audio.true_peak"):
            if audio_done:
                continue
            audio_done = True
        if key in seen:
            continue
        seen.add(key)
        try:
            with silenced():
                what = FIXES[issue.check](job, issue)
            done.append({"check": issue.check, "applied": what, "version": store.head(job)})
            log(f"fixed {issue.check}: {what} → v{store.head(job):03d}")
        except CutroomError as e:
            done.append({"check": issue.check, "failed": str(e)})
            log(f"could not fix {issue.check}: {e}")
    return done
