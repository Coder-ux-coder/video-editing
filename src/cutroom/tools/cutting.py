"""Transcript-driven cutting: remove-silence, remove-fillers, detect-retakes, cut, restore."""

from __future__ import annotations

import json
from typing import Annotated, Optional

import typer

from cutroom.cli_common import DryRunOpt, JobArg, JsonOpt, VersionOpt, emit, out
from cutroom.errors import JobError, RefusedError
from cutroom.job import atomic_write_text
from cutroom.timeline import store
from cutroom.timeline.ops import WORD_EDGE_TOL, Removal, kept_ranges, move_range, restore_cut
from cutroom.timeline.outline import tc
from cutroom.timeline.planning import detect_retakes as find_retakes
from cutroom.timeline.planning import (plan_fillers, plan_retakes, plan_sentence_cut, plan_silence,
                                      sentence_block)
from cutroom.tools._common import apply_and_save, open_edit, report
from cutroom.tools.registry import ToolManifest, tool

TESTS = ["tests/test_cutting.py"]


@tool(ToolManifest("remove-silence", "Shorten long pauses and trim head/tail, keeping natural rhythm.", "cutting",
                   inputs=["timeline HEAD", "words.json", "style editing.*pause*"],
                   outputs=["timeline vN+1"], tests=TESTS, changes_timeline=True))
def remove_silence(job_id: JobArg, version: VersionOpt = None,
                   max_pause: Annotated[Optional[float], typer.Option(help="Pauses longer than this are shortened (s). Default from style.")] = None,
                   keep: Annotated[Optional[float], typer.Option(help="Pause length to keep mid-sentence (s). Default from style.")] = None,
                   dry_run: DryRunOpt = False, as_json: JsonOpt = False) -> None:
    """Shorten every pause longer than the style's max_pause to a natural length (longer after a
    sentence end; possible dramatic pauses kept longer), and trim silence before the first and
    after the last word. Cuts always sit inside pauses, never inside words."""
    ctx = open_edit(job_id, version)
    if max_pause is not None:
        ctx.style["editing"]["max_pause"] = max_pause
    if keep is not None:
        ctx.style["editing"]["keep_pause_mid_sentence"] = keep
    removals = []
    for s in ctx.tl.sources:
        wf = ctx.words.get(s.id)
        if wf:
            removals += plan_silence(s.id, wf, kept_ranges(ctx.tl, s.id), ctx.ed, ctx.env(s.id), ctx.shots(s.id))
    report(apply_and_save(ctx, removals, tool="remove-silence", title="remove-silence", dry_run=dry_run), as_json)


@tool(ToolManifest("remove-fillers", "Remove um/uh (and optionally discourse markers) with natural joins.", "cutting",
                   inputs=["timeline HEAD", "words.json", "style editing.fillers_*"],
                   outputs=["timeline vN+1"], tests=TESTS, changes_timeline=True))
def remove_fillers(job_id: JobArg, version: VersionOpt = None,
                   contextual: Annotated[Optional[bool], typer.Option("--contextual/--no-contextual", help="Also remove discourse markers ('you know', 'I mean', 'like') when set off by commas. Default from style.")] = None,
                   acoustic: Annotated[Optional[bool], typer.Option("--acoustic/--no-acoustic", help="Also remove untranscribed voiced sounds in pauses (likely 'um'). Default from style.")] = None,
                   dry_run: DryRunOpt = False, as_json: JsonOpt = False) -> None:
    """Remove filler words. Pure hesitations (um, uh, er, hmm) always; discourse markers only when
    isolated by punctuation and enabled; meaningful uses ("I like it", "you know him") are
    protected because they are not set off by commas. Leaves a natural pause at each join."""
    ctx = open_edit(job_id, version)
    if contextual is not None:
        ctx.style["editing"]["remove_contextual_fillers"] = contextual
    if acoustic is not None:
        ctx.style["editing"]["remove_acoustic_fillers"] = acoustic
    removals = []
    for s in ctx.tl.sources:
        wf = ctx.words.get(s.id)
        if wf:
            removals += plan_fillers(s.id, wf, kept_ranges(ctx.tl, s.id), ctx.ed, ctx.env(s.id), ctx.shots(s.id))
    report(apply_and_save(ctx, removals, tool="remove-fillers", title="remove-fillers", dry_run=dry_run), as_json)


@tool(ToolManifest("detect-retakes", "Find stumbles, false starts and repeated sentences; optionally cut them.",
                   "cutting", inputs=["timeline HEAD", "words.json"],
                   outputs=["analysis/retakes.json", "timeline vN+1 with --apply"], tests=TESTS,
                   changes_timeline=True))
def detect_retakes(job_id: JobArg, version: VersionOpt = None,
                   apply: Annotated[bool, typer.Option(help="Cut the candidates at or above --min-confidence.")] = False,
                   min_confidence: Annotated[float, typer.Option(help="Threshold for --apply.")] = 0.75,
                   only: Annotated[Optional[list[str]], typer.Option("--only", help="Apply only these candidate ids (r001…).")] = None,
                   dry_run: DryRunOpt = False, as_json: JsonOpt = False) -> None:
    """Find abandoned attempts the speaker redid: immediate repetitions ("we are, we are"), false
    starts ("so the main — so the main thing is") and whole sentences said again. Keeps the last
    complete take unless it is clearly worse. Writes analysis/retakes.json with reasons; --apply
    cuts the confident ones (or --only the ids you choose)."""
    ctx = open_edit(job_id, version)
    all_cands, removals = [], []
    for s in ctx.tl.sources:
        wf = ctx.words.get(s.id)
        if not wf:
            continue
        kept = kept_ranges(ctx.tl, s.id)
        cands = find_retakes(wf, kept, ctx.ed)
        for c in cands:
            c.id = f"{s.id}-{c.id}"
        all_cands += [(s.id, c) for c in cands]
        chosen = [c for c in cands if (c.id in only if only else c.confidence >= min_confidence)]
        if apply or dry_run:
            removals += plan_retakes(s.id, wf, kept, ctx.ed, chosen, ctx.env(s.id), ctx.shots(s.id))
    data = {"base_version": ctx.base, "candidates": [{"source": sid, **c.to_dict()} for sid, c in all_cands]}
    atomic_write_text(ctx.job.root / "analysis" / "retakes.json", json.dumps(data, indent=1) + "\n")
    if not (apply or dry_run):
        if as_json:
            emit(True, data)
            return
        for d in data["candidates"]:
            out.print(f"{d['id']} {d['kind']} conf {d['confidence']:.2f} @ {tc(d['drop_from'])}: drop "
                      f"\"{d['drop_text']}\" | {d['reason']}", markup=False, highlight=False)
        out.print(f"{len(data['candidates'])} candidate(s) saved to analysis/retakes.json; "
                  f"cut with --apply (>= {min_confidence}) or --apply --only <id>.")
        return
    result = apply_and_save(ctx, removals, tool="detect-retakes", title="retakes", dry_run=dry_run)
    result["candidates"] = data["candidates"]
    report(result, as_json)


def _timeline_to_source(tl, t: float):
    for c in tl.track_clips():
        if c.start - 1e-6 <= t <= c.end + 1e-6:
            return c.source, c.to_source(t)
    raise JobError(f"Timeline time {t:.3f}s is outside the edit (0–{tl.duration:.3f}s).", fix="Check the time.")


def _parse_span(spec: str) -> tuple[int, int]:
    parts = spec.replace(" ", "").split("-")
    try:
        a = int(parts[0])
        b = int(parts[1]) if len(parts) > 1 and parts[1] else a
    except ValueError:
        raise RefusedError(f"Bad sentence range '{spec}'.", fix="Use e.g. --sentences 3 or --sentences 3-5.") from None
    return a, b


def _default_source(ctx, source: str | None) -> str:
    if source:
        return source
    if len(ctx.tl.sources) == 1:
        return ctx.tl.sources[0].id
    raise RefusedError("This timeline has several sources.", fix="Pass --source S0n.")


@tool(ToolManifest("cut", "Remove a range or whole sentences by hand, refusing mid-word cuts.", "cutting",
                   inputs=["timeline HEAD", "words.json"], outputs=["timeline vN+1"], tests=TESTS,
                   changes_timeline=True))
def cut(job_id: JobArg,
        reason: Annotated[str, typer.Option(help="Why (recorded in the timeline and log).")],
        start: Annotated[Optional[float], typer.Option("--from", help="Start of the range to remove (s).")] = None,
        end: Annotated[Optional[float], typer.Option("--to", help="End of the range to remove (s).")] = None,
        sentences: Annotated[Optional[str], typer.Option(help="Sentence numbers as in analysis/topics.task.md, e.g. 3 or 2-3. Leaves a natural pause.")] = None,
        speaker: Annotated[Optional[str], typer.Option(help="Cut every sentence of this speaker (id or name from `cutroom speakers`), e.g. the interviewer's questions.")] = None,
        source: Annotated[Optional[str], typer.Option(help="Source id: --from/--to are source times. Omit to use timeline times.")] = None,
        snap: Annotated[bool, typer.Option(help="Move edges that fall inside words to the nearest word boundary.")] = False,
        force: Annotated[bool, typer.Option(help="Allow cutting inside a word (not recommended).")] = False,
        version: VersionOpt = None, dry_run: DryRunOpt = False, as_json: JsonOpt = False) -> None:
    """Remove a range or whole sentences. With --sentences the cut covers exactly those sentences
    and leaves a natural pause. With --from/--to, edges must fall between words (--snap fixes
    them, --force overrides)."""
    ctx = open_edit(job_id, version)
    if speaker:
        from cutroom.perception import speakers as SP

        sid = _default_source(ctx, source)
        doc = SP.load(ctx.job, sid)
        if doc is None:
            raise RefusedError(f"No speakers found for {sid} yet.", fix=f"Run `cutroom speakers {ctx.job.id}` first.")
        ids = {k for k, v in doc.get("names", {}).items() if v == speaker} | {speaker}
        if not ids & {sp["id"] for sp in doc["speakers"]}:
            raise RefusedError(f"No speaker '{speaker}' in {sid}.", fix="See `cutroom speakers` for ids and names.")
        if doc.get("main") in ids:
            raise RefusedError(f"'{speaker}' is the main speaker of {sid}; cutting all of it would empty the edit.",
                               fix="Cut sentences with --sentences instead.")
        removals = []
        kept = kept_ranges(ctx.tl, sid)
        for x in doc["sentences"]:
            left = sum(max(0.0, min(b, x["end"]) - max(a, x["start"])) for a, b in kept)
            if x["speaker"] in ids and left >= 0.5 * (x["end"] - x["start"]):   # skip what is already cut
                r = plan_sentence_cut(sid, ctx.words[sid], kept, ctx.ed, x["n"], x["n"],
                                      reason, ctx.env(sid), ctx.shots(sid))
                if r is not None:
                    removals.append(r)
        if not removals:
            raise RefusedError(f"Nothing of '{speaker}' is left in the edit.", fix="Check `cutroom show`.")
        report(apply_and_save(ctx, removals, tool="cut", title=f"cut {len(removals)} sentence(s) of {speaker} ({reason})",
                              dry_run=dry_run), as_json)
        return
    if sentences:
        sid = _default_source(ctx, source)
        a_i, b_i = _parse_span(sentences)
        try:
            removal = plan_sentence_cut(sid, ctx.words[sid], kept_ranges(ctx.tl, sid), ctx.ed, a_i, b_i,
                                        reason, ctx.env(sid), ctx.shots(sid))
        except ValueError as e:
            raise RefusedError(str(e), fix="Sentence numbers are listed in analysis/topics.task.md.") from None
        if removal is None:
            raise RefusedError(f"Sentences {sentences} are not in the edit any more.", fix="Check `cutroom show`.")
        report(apply_and_save(ctx, [removal], tool="cut", title=f"cut sentences {sentences} ({reason})",
                              dry_run=dry_run), as_json)
        return
    if start is None or end is None:
        raise RefusedError("Give --sentences, or both --from and --to.", fix="See `cutroom cut --help`.")
    if source is None:
        sa, a = _timeline_to_source(ctx.tl, start)
        sb, b = _timeline_to_source(ctx.tl, end)
        if sa != sb:
            raise RefusedError("The range spans two sources.", fix="Cut each source separately with --source.")
        source = sa
    else:
        a, b = start, end
    if b <= a:
        raise RefusedError("--to must be after --from.", fix="Swap the values.")
    wf = ctx.words.get(source)
    if wf:
        for name in ("a", "b"):
            t = a if name == "a" else b
            inside = next((w for w in wf.words if w.start + WORD_EDGE_TOL < t < w.end - WORD_EDGE_TOL), None)
            if inside and snap:
                t = inside.start if (t - inside.start) < (inside.end - t) else inside.end
                a, b = (t, b) if name == "a" else (a, t)
            elif inside and not force:
                raise RefusedError(f"{t:.3f}s is inside the word '{inside.text}' ({inside.start:.3f}-{inside.end:.3f}).",
                                   fix="Use --snap to move to the nearest word boundary, or --force.")
    removal = Removal(source, a, b, kind="manual", reason=reason, confidence=1.0)
    report(apply_and_save(ctx, [removal], tool="cut", title=f"manual cut ({reason})", dry_run=dry_run), as_json)


@tool(ToolManifest("move", "Reorder: move sentences (or a source range) to another point in the edit.", "cutting",
                   inputs=["timeline HEAD", "words.json"], outputs=["timeline vN+1"], tests=TESTS,
                   changes_timeline=True))
def move(job_id: JobArg,
         reason: Annotated[str, typer.Option(help="Why (e.g. 'cold open hook').")],
         to: Annotated[float, typer.Option("--to", help="Timeline time to insert at (snapped to the nearest clip boundary); 0 = start.")],
         sentences: Annotated[Optional[str], typer.Option(help="Sentence numbers to move, e.g. 8-9.")] = None,
         start: Annotated[Optional[float], typer.Option("--from", help="Source start (with --until).")] = None,
         until: Annotated[Optional[float], typer.Option(help="Source end (with --from).")] = None,
         source: Annotated[Optional[str], typer.Option(help="Source id (default: the only source).")] = None,
         version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Move material to another position, e.g. a strong line to the very start as a cold open.
    With --sentences the block is lifted at the middle of the surrounding pauses, so both the
    gap it leaves and the moved block keep natural rhythm. Nothing is deleted."""
    ctx = open_edit(job_id, version)
    sid = _default_source(ctx, source)
    kept = kept_ranges(ctx.tl, sid)
    if sentences:
        try:
            a, b = sentence_block(ctx.words[sid], kept, *_parse_span(sentences), env=ctx.env(sid),
                                  shots=ctx.shots(sid), pad=ctx.ed["pad_before_word"])
        except ValueError as e:
            raise RefusedError(str(e), fix="Sentence numbers are listed in analysis/topics.task.md.") from None
    elif start is not None and until is not None:
        a, b = start, until
    else:
        raise RefusedError("Give --sentences, or --from and --until.", fix="See `cutroom move --help`.")
    tl = ctx.tl.model_copy(deep=True)
    moved, at = move_range(tl, sid, a, b, to, reason)
    from cutroom.timeline.ops import check_cut_points

    problems = check_cut_points(tl, ctx.words)
    if problems:
        raise RefusedError("The move would cut inside a word: " + problems[0], fix="Use --sentences, or adjust times.")
    text = ctx.words[sid].text_between(a, b) if ctx.words.get(sid) else ""
    summary = f"moved {sid} {tc(a)}–{tc(b)} to {tc(at)} ({reason}): \"{text[:60]}\""
    ctx.job.log.section(f"move (from v{ctx.base:03d})")
    n = store.save_new(ctx.job, tl, command="move", summary=summary, parent=ctx.base)
    emit(as_json, {"version": n, "moved_clips": [c.id for c in moved], "inserted_at_s": round(at, 3)})
    if not as_json:
        out.print(f"{summary} → v{n:03d}")


@tool(ToolManifest("restore", "Put a previously cut range back (by cut id).", "cutting",
                   inputs=["timeline HEAD"], outputs=["timeline vN+1"], tests=TESTS, changes_timeline=True))
def restore(job_id: JobArg, cut_ids: Annotated[list[str], typer.Argument(help="Cut ids, e.g. x0012 (see `show --format cuts`).")],
            version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Undo specific cuts without undoing whole versions, e.g. a filler that was really a word."""
    ctx = open_edit(job_id, version, need_words=False)
    tl = ctx.tl.model_copy(deep=True)
    restored = [restore_cut(tl, cid) for cid in cut_ids]
    summary = f"restored {', '.join(c.id for c in restored)} (+{sum(c.duration for c in restored):.2f}s)"
    ctx.job.log.section(f"restore (from v{ctx.base:03d})")
    n = store.save_new(ctx.job, tl, command="restore", summary=summary, parent=ctx.base)
    emit(as_json, {"version": n, "restored": [c.id for c in restored]})
    if not as_json:
        out.print(f"{summary} → v{n:03d}")


@tool(ToolManifest("extend", "Lengthen a clip into the removed pause next to it (never over words).", "cutting",
                   inputs=["timeline HEAD", "words.json"], outputs=["timeline vN+1"], tests=TESTS, changes_timeline=True))
def extend(job_id: JobArg, clip: Annotated[str, typer.Argument(help="Clip id (see `cutroom show`), or 'last' / 'first'.")],
           by: Annotated[float, typer.Option(help="Seconds to add.")],
           edge: Annotated[str, typer.Option(help="out (end) or in (start).")] = "out",
           reason: Annotated[str, typer.Option(help="Why.")] = "",
           version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Give a clip more air: e.g. let the last word ring out (`extend <job> last --by 0.4`), or
    lengthen a too-short shot. Only removed material without words can come back."""
    from cutroom.timeline.ops import extend_clip_edge

    ctx = open_edit(job_id, version)
    tl = ctx.tl.model_copy(deep=True)
    clips = tl.track_clips()
    cid = clips[-1].id if clip == "last" else (clips[0].id if clip == "first" else clip)
    if edge not in ("in", "out"):
        raise RefusedError(f"Bad edge '{edge}'.", fix="Use --edge out or --edge in.")
    cut = extend_clip_edge(tl, cid, edge, by, words=ctx.words)
    summary = f"extend {cid} {edge} by {by:.2f}s into {cut.id}" + (f" ({reason})" if reason else "")
    ctx.job.log.section(f"extend (from v{ctx.base:03d})")
    n = store.save_new(ctx.job, tl, command="extend", summary=summary, parent=ctx.base)
    emit(as_json, {"version": n, "clip": cid})
    if not as_json:
        out.print(f"{summary} → v{n:03d}")
