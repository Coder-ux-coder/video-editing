"""Motion-graphics commands: templates, graphic-add/-remove/-list, punch-in, transition, template-preview.

Graphics are data in the timeline (template id + parameters + placement + references), drawn by
Remotion at render time. Placement avoids faces and the caption band; numbers are verified
against the transcript or a provided document before a graphic is accepted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Optional

import typer

from cutroom.cli_common import JobArg, JsonOpt, VersionOpt, emit, out
from cutroom.errors import RefusedError
from cutroom.graphics.templates import TemplateSpec, get_template, load_templates, min_hold, validate_params
from cutroom.timeline import store
from cutroom.timeline.model import Graphic, Keyframe, Timeline
from cutroom.timeline.ops import source_to_timeline
from cutroom.timeline.outline import tc
from cutroom.tools._common import EditContext, open_edit
from cutroom.tools.registry import ToolManifest, tool

TESTS = ["tests/test_graphics.py"]


# --------------------------------------------------------------------------- helpers


def parse_time(s: str) -> float:
    """Seconds from '83.2', '1:23.2' or '0:01:23.2'."""
    try:
        parts = [float(p) for p in s.strip().split(":")]
    except ValueError:
        raise RefusedError(f"Bad time '{s}'.", fix="Use seconds (83.2) or m:ss (1:23.2).") from None
    t = 0.0
    for p in parts:
        t = t * 60 + p
    return t


def _parse_params(items: list[str] | None) -> dict:
    out_: dict = {}
    for it in items or []:
        if "=" not in it:
            raise RefusedError(f"Bad --param '{it}'.", fix="Use --param key=value.")
        k, v = it.split("=", 1)
        k = k.strip()
        v = v if k in ("prefix", "suffix") else v.strip()     # a suffix " years" keeps its space
        if v.startswith(("[", "{")):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                pass
        out_[k.strip()] = v
    return out_


def _timeline_text(ctx: EditContext, t0: float, t1: float) -> tuple[str, list[tuple[str, float, float]]]:
    """Words kept in timeline span [t0, t1] and the source spans they come from."""
    text, spans = [], []
    for c in ctx.tl.track_clips():
        if c.end <= t0 or c.start >= t1:
            continue
        a, b = c.to_source(max(t0, c.start)), c.to_source(min(t1, c.end))
        wf = ctx.words.get(c.source)
        if wf:
            text.append(wf.text_between(a, b))
        spans.append((c.source, a, b))
    return " ".join(text), spans


def resolve_refs(ctx: EditContext, refs: list[str] | None) -> tuple[dict[str, str], dict[str, str]]:
    """Fact references → ({canonical id: full text}, {canonical id: short quote})."""
    from cutroom.timeline.planning import sentence_words

    texts: dict[str, str] = {}
    for r in refs or []:
        r = r.strip()
        stored = re.fullmatch(r"transcript:(S\d+):([\d.]+)-([\d.]+)", r)
        if stored:                       # canonical form stored in the timeline
            r = f"{stored.group(1)}@{stored.group(2)}-{stored.group(3)}"
        m = re.fullmatch(r"(?:(S\d+):)?sentences?:(\d+)(?:-(\d+))?", r, flags=re.I)
        if m:
            sid = m.group(1) or _only_source(ctx.tl)
            a, b = int(m.group(2)), int(m.group(3) or m.group(2))
            wf = ctx.words.get(sid)
            if wf is None:
                raise RefusedError(f"No words for {sid}.", fix="Run `cutroom analyze` first.")
            try:
                ws = sentence_words(wf, a, b)
            except ValueError as e:
                raise RefusedError(str(e), fix="Sentence numbers are as in analysis/topics.task.md.") from None
            texts[f"transcript:{sid}:{ws[0].start:.2f}-{ws[-1].end:.2f}"] = " ".join(w.text for w in ws)
            continue
        m = re.fullmatch(r"(S\d+)@([\d.:]+)-([\d.:]+)", r, flags=re.I)
        if m:
            sid, a, b = m.group(1).upper(), parse_time(m.group(2)), parse_time(m.group(3))
            wf = ctx.words.get(sid)
            if wf is None:
                raise RefusedError(f"No words for {sid}.", fix="Run `cutroom analyze` first.")
            texts[f"transcript:{sid}:{a:.2f}-{b:.2f}"] = wf.text_between(a, b)
            continue
        m = re.fullmatch(r"t:([\d.:]+)-([\d.:]+)", r)
        if m:
            text, spans = _timeline_text(ctx, parse_time(m.group(1)), parse_time(m.group(2)))
            for sid, a, b in spans:
                texts[f"transcript:{sid}:{a:.2f}-{b:.2f}"] = ctx.words[sid].text_between(a, b) if ctx.words.get(sid) else ""
            continue
        if r == "brief" or r.startswith("doc:"):
            rel = "brief.md" if r == "brief" else r[4:]
            p = (ctx.job.root / rel).resolve()
            if ctx.job.root.resolve() not in p.parents or not p.is_file():
                raise RefusedError(f"Document '{rel}' is not a file inside this job.",
                                   fix="Put documents the client provided in the job's inputs/ folder.")
            if p.suffix.lower() not in (".md", ".txt", ".csv", ".json", ".srt", ".vtt"):
                raise RefusedError(f"Cannot read facts from {p.suffix} files.", fix="Save the document as .md or .txt.")
            texts[f"doc:{p.relative_to(ctx.job.root.resolve()).as_posix()}"] = p.read_text(encoding="utf-8", errors="replace")
            continue
        raise RefusedError(f"Bad fact reference '{r}'.",
                           fix="Use sentences:12 (or sentences:12-13), S01@83.2-90.5, t:1:23-1:30, brief, or doc:inputs/file.md.")
    quotes = {k: (v if len(v) <= 160 else v[:157] + "…") for k, v in texts.items() if k.startswith("transcript:")}
    return texts, quotes


def _only_source(tl: Timeline) -> str:
    if len(tl.sources) == 1:
        return tl.sources[0].id
    raise RefusedError("This timeline has several sources.", fix="Prefix the reference, e.g. S02:sentences:4.")


def _faces(ctx: EditContext):
    from cutroom.tools.captions_tools import _faces_lookup

    return _faces_lookup(ctx.job, ctx.tl)


def _region_luminance(ctx: EditContext, box, t0: float, t1: float) -> list[float] | None:
    """Footage under ``box`` at three looks (start, middle, end): [darkest 25th-percentile,
    brightest 75th-percentile] WCAG relative luminance. The render compares the theme's text and
    accent colours against both to decide on a backing panel."""
    from cutroom.tools.captions_tools import _brightness_lookup

    bright = _brightness_lookup(ctx.job, ctx.tl)
    lo, hi = [], []
    for t in (t0 + 0.3, (t0 + t1) / 2, max(t0, t1 - 0.3)):
        try:
            a = bright(t, box[1], box[3], box[0], box[2], pct=25)
            b = bright(t, box[1], box[3], box[0], box[2], pct=75)
        except Exception:      # no proxy or unreadable frame: no measurement, no backing
            a = b = None
        if a is not None and b is not None:
            lo.append(a)
            hi.append(b)
    return [round(min(lo), 4), round(max(hi), 4)] if lo else None


def _vertical_face_map(tl: Timeline):
    from cutroom.shorts.reframe import output_face_mapper

    return output_face_mapper(tl)


def _active(tl: Timeline, t0: float, t1: float, specs: dict[str, TemplateSpec], exclude: str | None = None) -> list[Graphic]:
    return [g for g in tl.graphics if g.end > t0 and g.start < t1 and g.id != exclude
            and not (specs.get(g.template) and specs[g.template].persistent)]


def build_graphic(ctx: EditContext, spec: TemplateSpec, *, start: float, duration: float | None, variant: str | None,
                  params: dict, refs: list[str] | None, anchor: str | None, reason: str, force: bool = False,
                  attach_t: tuple[str, float] | None = None) -> tuple[Graphic, list[str]]:
    """Validate, fact-check and place a graphic. Returns (graphic, warnings); refuses on errors."""
    from cutroom.graphics.facts import require_facts
    from cutroom.graphics.placement import caption_band, place

    tl = ctx.tl
    rules = ctx.style.get("graphics", {}).get("rules", {})
    theme = ctx.style.get("graphics", {}).get("theme", {})
    portrait = tl.meta.height > tl.meta.width
    if portrait and theme.get("safe_short"):
        theme = {**theme, "safe": theme["safe_short"]}
    params, variant = validate_params(spec, params, variant)
    texts, quotes = resolve_refs(ctx, refs)
    fc = require_facts(spec, params, texts)
    warnings: list[str] = []
    if fc.unsupported_words:
        warnings.append(f"words not in the references (paraphrase is fine; invention is not): "
                        f"{', '.join(fc.unsupported_words[:8])}")
    if spec.text_params and not texts and spec.zone not in ("corner", "edge") and spec.id not in (
            "subscribe-cta", "social-handle", "transition"):
        warnings.append("no --fact reference: on-screen text should cite the transcript span or document it comes from")
    hold = min_hold(spec, params, rules)
    if spec.persistent:
        start, end = 0.0, tl.duration
    else:
        dur = duration if duration is not None else max(spec.default_duration_s, hold)
        if dur + 1e-6 < hold:
            raise RefusedError(f"{dur:.1f}s is too short to read this {spec.id} (needs {hold:.1f}s: 1 s + 0.3 s per 3 words).",
                               fix=f"Use --duration {hold:.1f} or shorten the text.")
        end = min(tl.duration, start + dur)
        if end - start + 1e-6 < min(dur, hold):
            raise RefusedError(f"The video ends {tl.duration - start:.1f}s after {tc(start)}.", fix="Start earlier.")
    specs = load_templates()
    active = _active(tl, start, end, specs)
    if not spec.persistent and len(active) >= rules.get("max_simultaneous", 2) and not force:
        raise RefusedError(f"{len(active)} graphics are already on screen at {tc(start)}–{tc(end)} "
                           f"({', '.join(g.id for g in active)}); the limit is {rules.get('max_simultaneous', 2)}.",
                           fix="Move it, remove one, or pass --force with a reason.")
    others = [tuple(g.placement["box"]) for g in active if g.placement.get("box")]
    band = caption_band(ctx.style, tl.captions.style if tl.captions else None, tl.meta.width, tl.meta.height,
                        short=tl.meta.aspect == "9:16")
    pl = place(spec, start, end, faces=_faces(ctx), safe=theme.get("safe", {}), band=band, others=others,
               clearance=rules.get("caption_band_clearance", 0.02), anchor=anchor,
               face_map=_vertical_face_map(tl) if portrait else None)
    if pl.get("face_cover", 0) > 0.02:
        warnings.append(f"no anchor keeps it fully off the face ({pl['face_cover']:.0%} of the head covered)")
    if spec.hides_captions and tl.captions:
        n = sum(1 for c in tl.captions.cues for w in c.words if start <= w["start"] < end)
        if n > 2:
            warnings.append(f"burned-in captions are hidden while it is on screen ({n} spoken words); "
                            "fine when it shows the same words, otherwise place it over a pause")
    bg_lum = None
    if variant in spec.backing_variants:   # measured once; the panel is decided per theme at render
        bg_lum = _region_luminance(ctx, pl.get("box") or [0.1, 0.38, 0.9, 0.62], start, end)
    gid = tl.next_id("g", [g.id for g in tl.graphics])
    attach = None
    if not spec.persistent:
        if attach_t is None:
            for c in tl.track_clips():
                if c.start - 1e-6 <= start < c.end:
                    attach_t = (c.source, round(c.to_source(start), 4))
                    break
        if attach_t:
            at = source_to_timeline(tl, *attach_t)
            attach = {"source": attach_t[0], "t": attach_t[1], "offset": round(start - at, 4) if at is not None else 0.0}
    g = Graphic(id=gid, template=spec.id, variant=variant, params=params, start=round(start, 3), end=round(end, 3),
                anchor=anchor or "auto", placement={k: v for k, v in pl.items() if k in ("anchor", "x", "y", "box", "valign")},
                facts=list(texts), fact_check={**fc.as_dict(), "quotes": quotes, "placement": pl}, attach=attach,
                whole=spec.persistent, hides_captions=spec.hides_captions, bg_luminance=bg_lum, reason=reason)
    return g, warnings


def _save(ctx: EditContext, tl: Timeline, command: str, summary: str) -> int:
    ctx.job.log.section(f"{command} (from v{ctx.base:03d})")
    return store.save_new(ctx.job, tl, command=command, summary=summary, parent=ctx.base)


def _describe(g: Graphic) -> str:
    main = next((str(v) for k, v in g.params.items() if k in ("name", "title", "text", "label", "handle")), "")
    if g.template == "number-counter":
        main = f"{g.params.get('prefix', '')}{g.params.get('to')}{g.params.get('suffix', '')} {g.params.get('label', '')}"
    return f"{g.template}/{g.variant} {main!r}".strip()


# --------------------------------------------------------------------------- commands


@tool(ToolManifest("templates", "List motion-graphics templates, their variants and parameters.", "graphics",
                   inputs=["templates/*.json"], outputs=["template list"], tests=TESTS))
def templates(as_json: JsonOpt = False) -> None:
    """Every template the agent can add with `graphic-add`, with variants, parameters (* = required),
    which parameters are facts that must be sourced, and where it sits on screen."""
    specs = load_templates()
    if as_json:
        emit(True, {k: s.__dict__ for k, s in specs.items()})
        return
    for s in specs.values():
        ps = ", ".join(f"{k}{'*' if p.get('required') else ''}:{p['type']}" for k, p in s.params.items())
        facts = f"  facts: {', '.join(s.fact_params)}" if s.fact_params else ""
        out.print(f"[bold]{s.id}[/] ({' | '.join(s.variants)}) – {s.description}", highlight=False)
        out.print(f"    params: {ps}{facts}  zone: {s.zone}", markup=False, highlight=False)


@tool(ToolManifest("graphic-add", "Add a motion graphic from a template, fact-checked and placed off faces.", "graphics",
                   inputs=["timeline HEAD", "templates/*.json", "words.json", "faces.json", "style graphics.*",
                           "inputs/ documents"], outputs=["timeline vN+1 with the graphic"], tests=TESTS,
                   changes_timeline=True))
def graphic_add(job_id: JobArg,
                template: Annotated[str, typer.Argument(help="Template id (see `cutroom templates`).")],
                at: Annotated[Optional[str], typer.Option(help="Timeline time it appears (s or m:ss).")] = None,
                sentence: Annotated[Optional[int], typer.Option(help="Appear when this sentence starts (numbering as in topics.task.md).")] = None,
                duration: Annotated[Optional[float], typer.Option(help="Seconds on screen (default: template default, at least the reading time).")] = None,
                variant: Annotated[Optional[str], typer.Option(help="Template variant.")] = None,
                param: Annotated[Optional[list[str]], typer.Option("--param", "-p", help="key=value (repeat). Lists: a|b|c. Data: 2019:12,2020:30.")] = None,
                fact: Annotated[Optional[list[str]], typer.Option("--fact", "-f", help="Source of what it shows: sentences:12, S01@83.2-90.5, t:1:23-1:30, brief, doc:inputs/x.md (repeat).")] = None,
                anchor: Annotated[Optional[str], typer.Option(help="Force an anchor (e.g. bottom-right); default chooses off the face.")] = None,
                reason: Annotated[str, typer.Option(help="Why this graphic helps the viewer.")] = "",
                force: Annotated[bool, typer.Option(help="Allow more than the simultaneous-graphics limit.")] = False,
                version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Add a graphic. Numbers it shows must be found in its --fact references (transcript span or a
    document in the job), or it is refused. It is placed inside the safe area, above the caption
    band, on the side that covers the least of the face, holds at least the reading time, and
    is attached to the moment it appears, so later cuts move it with its content."""
    ctx = open_edit(job_id, version)
    spec = get_template(template)
    attach_t = None
    if sentence is not None:
        from cutroom.timeline.planning import sentence_words

        sid = _only_source(ctx.tl)
        try:
            w0 = sentence_words(ctx.words[sid], sentence, sentence)[0]
        except ValueError as e:
            raise RefusedError(str(e), fix="Sentence numbers are as in analysis/topics.task.md.") from None
        t = source_to_timeline(ctx.tl, sid, w0.start)
        if t is None:
            raise RefusedError(f"Sentence {sentence} is not in the edit.", fix="Check `cutroom show`.")
        start, attach_t = t, (sid, w0.start)
    elif at is not None:
        start = parse_time(at)
    elif spec.persistent:
        start = 0.0
    else:
        raise RefusedError("Say when it appears.", fix="Pass --at 1:23 or --sentence N.")
    if not 0 <= start < ctx.tl.duration:
        raise RefusedError(f"{tc(start)} is outside the edit (0–{tc(ctx.tl.duration)}).", fix="Check the time.")
    g, warnings = build_graphic(ctx, spec, start=start, duration=duration, variant=variant,
                                params=_parse_params(param), refs=fact, anchor=anchor, reason=reason, force=force,
                                attach_t=attach_t)
    tl = ctx.tl.model_copy(deep=True)
    tl.graphics.append(g)
    summary = f"graphic {g.id} {_describe(g)} at {tc(g.start)}–{tc(g.end)} ({g.placement.get('anchor')})"
    if g.facts:
        summary += f"; source: {', '.join(g.facts)}"
    n = _save(ctx, tl, "graphic-add", summary)
    for w in warnings:
        ctx.job.log.warn(f"{g.id}: {w}")
    emit(as_json, {"version": n, "graphic": g.model_dump(), "warnings": warnings})
    if not as_json:
        out.print(f"{summary} → v{n:03d}", markup=False, highlight=False)
        for w in warnings:
            out.print(f"[yellow]  ! {w}[/]")


@tool(ToolManifest("graphic-remove", "Remove a graphic by id.", "graphics", inputs=["timeline HEAD"],
                   outputs=["timeline vN+1"], tests=TESTS, changes_timeline=True))
def graphic_remove(job_id: JobArg, graphic_id: Annotated[str, typer.Argument(help="Graphic id, e.g. g0003.")],
                   version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Remove one graphic (the earlier version keeps it; `cutroom undo` brings it back)."""
    ctx = open_edit(job_id, version, need_words=False)
    tl = ctx.tl.model_copy(deep=True)
    g = next((g for g in tl.graphics if g.id == graphic_id), None)
    if g is None:
        raise RefusedError(f"No graphic {graphic_id}.", fix="See `cutroom graphic-list`.")
    tl.graphics.remove(g)
    n = _save(ctx, tl, "graphic-remove", f"removed graphic {g.id} {_describe(g)}")
    emit(as_json, {"version": n, "removed": g.id})
    if not as_json:
        out.print(f"removed {g.id} → v{n:03d}")


@tool(ToolManifest("graphic-move", "Retime or re-place a graphic (keeps its parameters and sources).", "graphics",
                   inputs=["timeline HEAD", "faces.json"], outputs=["timeline vN+1"], tests=TESTS, changes_timeline=True))
def graphic_move(job_id: JobArg, graphic_id: Annotated[str, typer.Argument(help="Graphic id, e.g. g0003.")],
                 at: Annotated[Optional[str], typer.Option(help="New start (timeline s or m:ss).")] = None,
                 duration: Annotated[Optional[float], typer.Option(help="New length (s).")] = None,
                 anchor: Annotated[Optional[str], typer.Option(help="Force an anchor.")] = None,
                 replace: Annotated[bool, typer.Option("--replace", help="Choose a different anchor than now (after a face or safe-area failure).")] = False,
                 version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Move a graphic in time or on screen. Facts are re-checked against its stored references and
    placement is recomputed (off the face, above the captions, away from other graphics)."""
    ctx = open_edit(job_id, version)
    tl = ctx.tl.model_copy(deep=True)
    old = next((g for g in tl.graphics if g.id == graphic_id), None)
    if old is None:
        raise RefusedError(f"No graphic {graphic_id}.", fix="See `cutroom graphic-list`.")
    spec = get_template(old.template)
    if replace and not anchor:
        others = [a for a in spec.anchors if a != old.placement.get("anchor")]
        if not others:
            raise RefusedError(f"{old.template} has no other anchor.", fix="Move it in time (--at) or remove it.")
        anchor = None
    tl.graphics = [g for g in tl.graphics if g.id != graphic_id]
    work = EditContext(ctx.job, tl, ctx.base, ctx.words, ctx.style, ctx.envs)
    start = parse_time(at) if at is not None else old.start
    attach_t = None if at is not None or not old.attach else (old.attach["source"], old.attach["t"])
    if replace and not anchor:
        # try the other anchors, keep the one covering the least face
        best = None
        for a in [x for x in spec.anchors if x != old.placement.get("anchor")]:
            g, w = build_graphic(work, spec, start=start, duration=duration if duration is not None else old.end - old.start,
                                 variant=old.variant, params=old.params, refs=old.facts, anchor=a, reason=old.reason,
                                 force=True, attach_t=attach_t)
            cover = g.fact_check.get("placement", {}).get("face_cover", 0)
            if best is None or cover < best[0]:
                best = (cover, g, w)
        _, g, warnings = best
    else:
        g, warnings = build_graphic(work, spec, start=start, duration=duration if duration is not None else old.end - old.start,
                                    variant=old.variant, params=old.params, refs=old.facts, anchor=anchor,
                                    reason=old.reason, force=True, attach_t=attach_t)
    g.id, g.z, g.backing = old.id, old.z, old.backing
    if old.attach and attach_t is not None and g.attach:
        g.attach["offset"] = old.attach.get("offset", 0.0)
    tl.graphics.append(g)
    summary = (f"moved graphic {g.id} {_describe(g)} to {tc(g.start)}–{tc(g.end)} ({g.placement.get('anchor')})")
    n = _save(ctx, tl, "graphic-move", summary)
    emit(as_json, {"version": n, "graphic": g.model_dump(), "warnings": warnings})
    if not as_json:
        out.print(f"{summary} → v{n:03d}", markup=False, highlight=False)
        for w in warnings:
            out.print(f"[yellow]  ! {w}[/]")


@tool(ToolManifest("graphic-list", "List the graphics in a timeline version with timing, placement and sources.",
                   "graphics", inputs=["timeline version"], outputs=["graphics list"], tests=TESTS))
def graphic_list(job_id: JobArg, version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Every graphic: id, template/variant, what it shows, when, where, and its fact references."""
    ctx = open_edit(job_id, version, need_words=False)
    gs = sorted(ctx.tl.graphics, key=lambda g: g.start)
    if as_json:
        emit(True, {"version": ctx.base, "graphics": [g.model_dump() for g in gs]})
        return
    if not gs:
        out.print("No graphics.")
    for g in gs:
        flag = " [red](detached)[/]" if g.attach and g.attach.get("detached") else ""
        out.print(f"{g.id} {tc(g.start)}–{tc(g.end)} {g.placement.get('anchor', '')} {_describe(g)}{flag}",
                  highlight=False)
        for f in g.facts:
            q = g.fact_check.get("quotes", {}).get(f, "")
            out.print(f"    source {f} {('“' + q + '”') if q else ''}", markup=False, highlight=False)


# --------------------------------------------------------------------------- punch-ins and transitions


def _shot_index(starts: list[float], t: float) -> int:
    return sum(1 for s in starts if s <= t + 1e-6)


def plan_punch_ins(tl: Timeline, faces_by_source: dict[str, list[dict]], shots_by_source: dict[str, list[float]],
                   scale: float, min_clip: float = 0.8) -> dict[str, tuple[float, float, float]]:
    """Jump-cut disguise: alternate 100 % / ``scale`` at every join that stays on the same camera
    angle (same source, same source shot, later in time). A join on a camera change resets to
    100 %; clips shorter than ``min_clip`` keep the previous framing (a zoom that short reads as a
    jolt). Returns {clip id: (scale, centre x, centre y)} for the clips to punch in, centred on the face."""
    clips = tl.track_clips()
    plan: dict[str, tuple[float, float, float]] = {}
    cur = 1.0
    for prev, c in zip([None, *clips], clips):
        if prev is None:
            cur = 1.0
        else:
            starts = shots_by_source.get(c.source, [])
            jump = (prev.source == c.source and c.src_in >= prev.src_out - 1e-3
                    and _shot_index(starts, prev.src_out - 0.04) == _shot_index(starts, c.src_in + 0.04))
            if not jump:
                cur = 1.0
            elif c.duration >= min_clip:
                cur = scale if cur == 1.0 else 1.0
        if cur > 1.0:
            plan[c.id] = (cur, *_crop_centre(faces_by_source.get(c.source, []), c.src_in, c.src_out, cur))
    return plan


def _crop_centre(samples: list[dict], a: float, b: float, scale: float) -> tuple[float, float]:
    """Centre of the punched-in crop: on the face, keeping the whole head and some headroom inside."""
    fs = [s["faces"][0] for s in samples if a - 0.5 <= s["t"] <= b + 0.5 and s["faces"]]
    if not fs:
        return 0.5, 0.5
    fs = fs[: max(1, min(len(fs), 4))]     # the framing at the cut is what the viewer compares
    cx = sum(f["x"] + f["w"] / 2 for f in fs) / len(fs)
    top = min(f["y"] for f in fs)
    cy = sum(f["y"] + f["h"] / 2 for f in fs) / len(fs)
    half = 0.5 / scale
    cx = min(max(cx, half), 1 - half)
    # keep the top of the head (with hair, ~25 % of the face height) at least 4 % inside the crop
    head_top = top - 0.25 * sum(f["h"] for f in fs) / len(fs)
    cy = min(cy, head_top - 0.04 + half)
    cy = min(max(cy, half), 1 - half)
    return round(cx, 4), round(cy, 4)


@tool(ToolManifest("punch-in", "Disguise jump cuts with alternating punch-ins (or set one clip's zoom).", "graphics",
                   inputs=["timeline HEAD", "faces.json", "visual_report.json shots"],
                   outputs=["timeline vN+1 with clip reframes"], tests=TESTS, changes_timeline=True))
def punch_in(job_id: JobArg,
             auto: Annotated[bool, typer.Option("--auto", help="Alternate framing at every same-angle jump cut.")] = False,
             clip: Annotated[Optional[str], typer.Option(help="One clip id to reframe.")] = None,
             scale: Annotated[Optional[float], typer.Option(help="Zoom factor (default style graphics.rules.punch_in_scale).")] = None,
             push_to: Annotated[Optional[float], typer.Option(help="With --clip: slow push from --scale to this zoom over the clip.")] = None,
             clear: Annotated[bool, typer.Option(help="Remove all reframes.")] = False,
             version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """A jump cut on the same camera angle looks like a glitch; alternating the framing (100 % ↔
    112 %) at those cuts reads as a deliberate camera change. Joins that already fall on a camera
    change are left alone. The crop is centred on the face, keeping the head inside the frame."""
    ctx = open_edit(job_id, version, need_words=False)
    tl = ctx.tl.model_copy(deep=True)
    if tl.meta.height > tl.meta.width:
        raise RefusedError("This is a vertical Short: its clips carry the 9:16 crop, which a punch-in would replace.",
                           fix="Jump cuts are the accepted style in Shorts; re-frame with `short-create` if needed.")
    s = scale or ctx.style.get("graphics", {}).get("rules", {}).get("punch_in_scale", 1.12)
    if not 1.0 <= s <= 1.5:
        raise RefusedError(f"Zoom {s} is outside 1.0-1.5.", fix="Punch-ins look natural at 1.08-1.2.")
    faces = {}
    for src in tl.sources:
        p = ctx.job.root / "analysis" / src.id / "faces.json"
        faces[src.id] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
    if clear:
        n_changed = sum(1 for c in tl.clips if c.reframe)
        for c in tl.clips:
            c.reframe = []
        summary = f"punch-in: cleared {n_changed} reframes"
    elif clip:
        c = next((c for c in tl.clips if c.id == clip), None)
        if c is None:
            raise RefusedError(f"No clip {clip}.", fix="See `cutroom show`.")
        cx, cy = _crop_centre(faces.get(c.source, []), c.src_in, c.src_out, max(s, push_to or s))
        c.reframe = [Keyframe(t=0.0, x=cx, y=cy, scale=s)]
        if push_to:
            c.reframe.append(Keyframe(t=round(c.duration, 3), x=cx, y=cy, scale=push_to, ease="linear"))
        n_changed = 1
        summary = f"punch-in: clip {c.id} at {s:.2f}" + (f" pushing to {push_to:.2f}" if push_to else "")
    elif auto:
        shots = {src.id: ctx.shots(src.id) for src in tl.sources}
        plan = plan_punch_ins(tl, faces, shots, s)
        for c in tl.clips:
            if c.locked:
                continue
            c.reframe = [Keyframe(t=0.0, x=plan[c.id][1], y=plan[c.id][2], scale=plan[c.id][0])] if c.id in plan else []
        n_changed = len(plan)
        joins = len(tl.track_clips()) - 1
        summary = f"punch-in --auto: {n_changed} of {len(tl.track_clips())} clips at {s:.2f} ({joins} joins)"
    else:
        raise RefusedError("Nothing to do.", fix="Pass --auto, --clip ID or --clear.")
    n = _save(ctx, tl, "punch-in", summary)
    emit(as_json, {"version": n, "changed": n_changed, "summary": summary})
    if not as_json:
        out.print(f"{summary} → v{n:03d}")


@tool(ToolManifest("transition", "Put an overlay transition (dip, flash, whip, swipe, glitch) on a cut.", "graphics",
                   inputs=["timeline HEAD"], outputs=["timeline vN+1"], tests=TESTS, changes_timeline=True))
def transition(job_id: JobArg,
               at: Annotated[Optional[str], typer.Option(help="Timeline time; the nearest cut is used.")] = None,
               join: Annotated[Optional[int], typer.Option(help="Cut number (1 = between the first and second clip).")] = None,
               variant: Annotated[str, typer.Option(help="dip, flash, whip, swipe or glitch.")] = "dip",
               duration: Annotated[Optional[float], typer.Option(help="Seconds, centred on the cut.")] = None,
               reason: Annotated[str, typer.Option(help="Why (topic change, time jump…).")] = "",
               version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Most cuts should stay plain cuts; use a transition to mark a topic change or a jump in time.
    It is drawn over the cut and moves with it if the edit changes."""
    ctx = open_edit(job_id, version, need_words=False)
    clips = ctx.tl.track_clips()
    if len(clips) < 2:
        raise RefusedError("There are no cuts.", fix="Cut first.")
    if join is not None:
        if not 1 <= join < len(clips):
            raise RefusedError(f"Cut {join} does not exist (1–{len(clips) - 1}).", fix="Check `cutroom show`.")
        nxt = clips[join]
    elif at is not None:
        t = parse_time(at)
        nxt = min(clips[1:], key=lambda c: abs(c.start - t))
    else:
        raise RefusedError("Say which cut.", fix="Pass --at 2:10 or --join 5.")
    spec = get_template("transition")
    d = duration or spec.default_duration_s
    start = max(0.0, nxt.start - d / 2)
    g, warnings = build_graphic(ctx, spec, start=start, duration=d, variant=variant, params={}, refs=None,
                                anchor=None, reason=reason or "transition at a cut", force=True,
                                attach_t=(nxt.source, round(nxt._q(nxt.src_in), 4)))
    g.attach["offset"] = round(-d / 2, 4)
    tl = ctx.tl.model_copy(deep=True)
    tl.graphics.append(g)
    n = _save(ctx, tl, "transition", f"transition {g.id} {variant} at the cut at {tc(nxt.start)}")
    emit(as_json, {"version": n, "graphic": g.model_dump()})
    if not as_json:
        out.print(f"transition {g.id} {variant} at {tc(nxt.start)} → v{n:03d}")


# --------------------------------------------------------------------------- previews


def preview_props(items: list[dict], theme: dict, width: int, height: int, fps: float, hold_s: float = 2.0) -> tuple[dict, list[int]]:
    """Overlay props showing each item ({template, variant, params}) in turn; returns the frame to look at for each."""
    specs = load_templates()
    graphics, frames = [], []
    slot = int(round(hold_s * fps))
    from cutroom.graphics.placement import candidate_box, placement_from_box

    safe = theme.get("safe", {"left": 0.06, "right": 0.94, "top": 0.07, "bottom": 0.93})
    for i, it in enumerate(items):
        spec = specs[it["template"]]
        params, variant = validate_params(spec, it.get("params", {}), it.get("variant"))
        params = {**spec.props, **params}
        pl = {"anchor": "center-center", "x": 0.5, "y": 0.5}
        if spec.anchors and spec.size_hint:
            a = spec.anchors[0]
            pl = placement_from_box(a, candidate_box(a, spec.size_hint["w"], spec.size_hint["h"], safe,
                                                     (0.77, 0.90) if spec.zone != "corner" else None, 0.02))
        if spec.id == "progress-bar":
            params = {**params, "total": slot * 2, "chapters": [slot // 2]}
        f0 = i * slot
        length = int(round(spec.default_duration_s * fps)) if spec.at_cut else slot
        graphics.append({"id": f"p{i}", "component": spec.component, "variant": variant, "from": f0, "to": f0 + length,
                         "params": params, "placement": pl, "z": 0, "hides_captions": False})
        frames.append(f0 + length // 2)
    return {"width": width, "height": height, "fps": fps, "durationInFrames": slot * len(items), "theme": theme,
            "graphics": graphics, "captions": None}, frames


def sample_params(spec: TemplateSpec) -> dict:
    """Plausible demo parameters for previews and visual tests (not for real videos)."""
    demo = {"name": "Jordan Rivera", "title": "Field Geologist", "text": "Tracks are evidence of behaviour",
            "label": "Fossil tracks found", "handle": "@cutroom", "platform": "Channel", "subtitle": "Chapter one",
            "unit": "", "src": None}
    out_: dict = {}
    for k, p in spec.params.items():
        t = p["type"]
        if t == "string":
            v = demo.get(k, k.capitalize())
            out_[k] = v[: p.get("max_len", 200)]
        elif t == "number":
            out_[k] = 1500 if k == "to" else 0
        elif t == "integer":
            out_[k] = {"number": 2, "step": 1, "decimals": 0}.get(k, 1)
        elif t == "rect":
            out_[k] = {"x": 0.58, "y": 0.22, "w": 0.28, "h": 0.22}
        elif t == "list":
            out_[k] = ["Find the trackway", "Measure each print", "Compare the stride"] if k != "emphasis" else ["evidence"]
        elif t == "data":
            out_[k] = [{"label": "2019", "value": 12}, {"label": "2020", "value": 30}, {"label": "2021", "value": 45}]
        elif t == "column":
            out_[k] = {"title": "Left" if k == "left" else "Right", "items": ["Fast", "Cheap"] if k == "left" else ["Slow", "Exact"]}
        elif t == "enum":
            out_[k] = p["values"][0]
    out_ = {k: v for k, v in out_.items() if v is not None and k not in ("chapters", "opacity", "color")}
    special = {"logo-bug": {"text": "CUTROOM"}, "subscribe-cta": {},
               "number-counter": {"to": 1500, "suffix": "+", "label": "Fossil tracks found"},
               "chart": {"data": out_.get("data"), "title": "Tracks per year"},
               "list-reveal": {"title": "How to read a trackway", "items": out_.get("items")}}
    return special.get(spec.id, out_)


def render_previews(items: list[dict], theme: dict, out_dir: Path, *, width: int = 640, height: int = 360,
                    fps: float = 30.0, background: Path | None = None) -> list[Path]:
    """Render one still per item (composited over a background) to ``out_dir``; returns the PNG paths."""
    import cv2
    import numpy as np

    from cutroom.graphics.remotion import render_overlay

    props, frames = preview_props(items, theme, width, height, fps)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / ".frames"
    render_overlay(props, raw, composition="Overlay", frames=frames, scratch=out_dir)
    if background is not None and background.is_file():
        bg = cv2.resize(cv2.imread(str(background)), (width, height))
    else:   # mid-grey gradient: shows both light and dark parts of a graphic
        ramp = np.linspace(60, 150, width, dtype=np.float32)
        bg = np.repeat(np.repeat(ramp[None, :, None], height, 0), 3, 2).astype(np.uint8)
    paths = []
    for it, f in zip(items, frames):
        img = cv2.imread(str(raw / f"{f}.png"), cv2.IMREAD_UNCHANGED)
        if img.shape[2] == 3:      # fully opaque frames are written without an alpha channel
            img = np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])
        a = img[:, :, 3:4].astype(np.float32) / 255
        comp = (img[:, :, :3].astype(np.float32) * a + bg.astype(np.float32) * (1 - a)).astype(np.uint8)
        p = out_dir / f"{it['template']}_{it.get('variant') or 'default'}.png"
        cv2.imwrite(str(p), comp)
        paths.append(p)
    import shutil

    shutil.rmtree(raw, ignore_errors=True)
    return paths


@tool(ToolManifest("template-preview", "Render still previews of graphics templates to look at before using them.",
                   "graphics", inputs=["templates/*.json", "style graphics.theme"], outputs=["PNG stills"], tests=TESTS))
def template_preview(template: Annotated[Optional[str], typer.Argument(help="Template id (default: all).")] = None,
                     variant: Annotated[Optional[str], typer.Option(help="One variant (default: all).")] = None,
                     param: Annotated[Optional[list[str]], typer.Option("--param", "-p", help="key=value (default: demo values).")] = None,
                     style: Annotated[str, typer.Option(help="Style file name.")] = "default",
                     client: Annotated[Optional[str], typer.Option(help="Client style (styles/clients/<name>.style.yaml).")] = None,
                     background: Annotated[Optional[Path], typer.Option(help="Image to draw over (e.g. a frame of the footage).")] = None,
                     out_dir: Annotated[Path, typer.Option("--out", help="Folder for the PNGs.")] = Path("template_previews"),
                     as_json: JsonOpt = False) -> None:
    """Draw each template variant at mid-hold with the theme of a style (and client), so the look
    can be checked by eye before graphics go into an edit."""
    from cutroom.style import load_style

    st = load_style(style, client=client)
    specs = load_templates()
    chosen = [specs[template]] if template else list(specs.values())
    if template and template not in specs:
        raise RefusedError(f"Unknown template '{template}'.", fix=f"Known: {', '.join(specs)}")
    items = []
    for s in chosen:
        for v in ([variant] if variant else s.variants):
            items.append({"template": s.id, "variant": v, "params": {**sample_params(s), **_parse_params(param)}})
    paths = render_previews(items, st.get("graphics", {}).get("theme", {}), out_dir, background=background)
    emit(as_json, {"previews": [str(p) for p in paths]})
    if not as_json:
        for p in paths:
            out.print(str(p))
