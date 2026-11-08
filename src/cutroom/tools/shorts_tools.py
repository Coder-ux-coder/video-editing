"""Shorts commands: shorts-find, short-create, shorts (batch), short-publish (title/description task)."""

from __future__ import annotations

import json
import re
from typing import Annotated, Optional

import typer

from cutroom.cli_common import JobArg, JsonOpt, VersionOpt, emit, out, silenced
from cutroom.errors import RefusedError
from cutroom.job import Job, atomic_write_text
from cutroom.timeline import store
from cutroom.timeline.outline import tc
from cutroom.tools.registry import ToolManifest, tool

TESTS = ["tests/test_shorts.py"]


def _candidates(job: Job, version: str | None, count: int, min_s: float | None, max_s: float | None):
    from cutroom.shorts.find import edit_sentences, find_candidates, pick
    from cutroom.style import timeline_style
    from cutroom.timeline.ops import words_for

    n = store.resolve(job, version)
    tl = store.load(job, n)
    cfg = dict(timeline_style(tl).get("shorts", {}))
    if min_s:
        cfg["min_s"] = min_s
    if max_s:
        cfg["max_s"] = max_s
    words = {s.id: words_for(job, s.id) for s in tl.sources}
    tp = job.root / "analysis" / "topics.json"
    topics = json.loads(tp.read_text(encoding="utf-8")).get("topics", []) if tp.is_file() else []
    from cutroom.perception.speakers import speaker_maps

    sents = edit_sentences(tl, words, speaker_maps(job, tl))
    cands = find_candidates(sents, topics, cfg, tl)
    return n, tl, sents, cands, pick(cands, count)


def _padded(job: Job, c, head: float = 0.12, tail: float = 0.4) -> tuple[float, float]:
    """Source range with breathing room, never reaching into neighbouring words."""
    from cutroom.timeline.ops import words_for

    wf = words_for(job, c["source"])
    before = [w.end for w in wf.words if w.end <= c["start"] + 1e-3] if wf else []
    after = [w.start for w in wf.words if w.start >= c["end"] - 1e-3] if wf else []
    lo = (max(before) + 0.03) if before else 0.0
    hi = (min(after) - 0.03) if after else c["end"] + tail
    return max(lo, c["start"] - head), min(hi, c["end"] + tail)


@tool(ToolManifest("shorts-find", "Score self-contained moments in an edit as Short candidates.", "shorts",
                   inputs=["timeline version", "words.json", "topics.json"], outputs=["analysis/shorts.json"],
                   tests=TESTS))
def shorts_find(job_id: JobArg, count: Annotated[int, typer.Option(help="How many to pick.")] = 5,
                min_s: Annotated[Optional[float], typer.Option(help="Shortest (s), default from style shorts.min_s.")] = None,
                max_s: Annotated[Optional[float], typer.Option(help="Longest (s), default from style shorts.max_s.")] = None,
                version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Windows of whole sentences scored for a first line that stops the scroll (no "and/so/it…"
    needing context, strong words, a question or a number), a clean ending, length near the target,
    one topic (preferring topics marked as Short candidates) and substance. Writes the ranked list
    and the non-overlapping picks to analysis/shorts.json; read the reasons before creating."""
    job = Job.load(job_id)
    n, tl, sents, cands, picks = _candidates(job, version, count, min_s, max_s)
    data = {"version": n, "picks": [c.as_dict() for c in picks], "ranked": [c.as_dict() for c in cands[:40]]}
    atomic_write_text(job.root / "analysis" / "shorts.json", json.dumps(data, indent=1) + "\n")
    emit(as_json, data)
    if not as_json:
        for k, c in enumerate(picks, 1):
            out.print(f"[bold]{k}[/] {c.source} {tc(c.start)}–{tc(c.end)} ({c.duration:.0f}s) score {c.score:.2f}: "
                      f"“{c.sentences[0].text[:90]}”", highlight=False)
            if c.reasons:
                out.print(f"    {'; '.join(c.reasons)}", markup=False, highlight=False)
        out.print(f"{len(cands)} windows scored; picks saved to analysis/shorts.json (create with `short-create --pick K`).")


@tool(ToolManifest("short-create", "Create a 9:16 Short job from a picked moment or a timeline range.", "shorts",
                   inputs=["timeline version", "analysis/shorts.json", "faces.json"],
                   outputs=["job <parent>.short-NN with timeline v001 (reframed 9:16)"], tests=TESTS,
                   changes_timeline=True))
def short_create(job_id: JobArg,
                 pick_: Annotated[Optional[int], typer.Option("--pick", help="Pick number from shorts-find (1-based).")] = None,
                 start: Annotated[Optional[float], typer.Option("--from", help="Source start (s) instead of a pick.")] = None,
                 end: Annotated[Optional[float], typer.Option("--to", help="Source end (s).")] = None,
                 source: Annotated[Optional[str], typer.Option(help="Source id for --from/--to (default: the only one).")] = None,
                 number: Annotated[Optional[int], typer.Option(help="Short number NN (default: next free).")] = None,
                 title: Annotated[Optional[str], typer.Option(help="Working title.")] = None,
                 version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Make job <job>.short-NN from the parent's cleaned edit in that range: same sources (no copy),
    analysis shared, timeline reframed 9:16 per clip (a still or smoothly moving crop on the face,
    or a blurred-fill layout when there is no single clear face)."""
    from cutroom.shorts.derive import derive_short

    job = Job.load(job_id)
    info: dict = {}
    if pick_ is not None:
        p = job.root / "analysis" / "shorts.json"
        if not p.is_file():
            raise RefusedError("No picks yet.", fix=f"Run `cutroom shorts-find {job.id}` first.")
        data = json.loads(p.read_text(encoding="utf-8"))
        if not 1 <= pick_ <= len(data["picks"]):
            raise RefusedError(f"Pick {pick_} does not exist (1–{len(data['picks'])}).", fix="See analysis/shorts.json.")
        c = data["picks"][pick_ - 1]
        a, b = _padded(job, c)
        src = c["source"]
        info = {"pick": pick_, "score": c["score"], "first_line": c["first_line"], "sentences": c["sentences"]}
        version = f"v{data['version']:03d}"
    elif start is not None and end is not None:
        a, b = start, end
        src = source or (job.manifest.sources[0].id if len(job.manifest.sources) == 1 else None)
        if src is None:
            raise RefusedError("This job has several sources.", fix="Pass --source S0n.")
    else:
        raise RefusedError("Say which moment.", fix="Pass --pick K (after shorts-find) or --from/--to.")
    n = store.resolve(job, version)
    k = number or _next_number(job)
    child = derive_short(job, n, src, a, b, k=k, title=title, info=info)
    tl = store.load(child)
    emit(as_json, {"job": child.id, "duration": round(tl.duration, 2), "source": src, "range": [a, b]})
    if not as_json:
        out.print(f"Short {child.id}: {src} {tc(a)}–{tc(b)} of v{n:03d}, {tl.duration:.1f}s, {len(tl.clips)} clips → v001")


def _next_number(job: Job) -> int:
    k = 1
    while (job.root.parent / f"{job.id}.short-{k:02d}" / "job.json").exists():
        k += 1
    return k


def _hook_text(text: str, max_words: int = 8, max_chars: int = 48) -> str:
    """The hook as the speaker said it: the first sentence if short, else its first clause, else
    its first words (never rewritten)."""
    t = re.sub(r"\s+", " ", text).strip()
    words = t.split()
    if len(words) <= max_words and len(t) <= max_chars:
        return t
    clause = re.split(r"(?<=[,;:])\s", t)[0].rstrip(",;:")
    if 3 <= len(clause.split()) <= max_words and len(clause) <= max_chars:
        return clause
    out_ = []
    for w in words:
        if len(" ".join(out_ + [w])) > max_chars - 1 or len(out_) == max_words:
            break
        out_.append(w)
    return " ".join(out_).rstrip(",;:") + "…"


@tool(ToolManifest("shorts", "Batch: find N Shorts, create them, caption them for 9:16, add the hook headline.",
                   "shorts", inputs=["timeline version"], outputs=["N Short jobs, each with captions and a headline"],
                   tests=TESTS, changes_timeline=True))
def shorts(job_id: JobArg, count: Annotated[int, typer.Option(help="How many Shorts.")] = 5,
           version: VersionOpt = None, render: Annotated[bool, typer.Option("--render/--no-render", help="Render previews.")] = False,
           as_json: JsonOpt = False) -> None:
    """shorts-find → short-create for each pick → captions in the Shorts style and safe zone →
    a headline with the opening line (the speaker's own words, sourced) → optional preview.
    The agent then reviews each Short (`cutroom review <short job>`) and writes its title and
    description (`short-publish`)."""
    from cutroom.render.engine import render as do_render
    from cutroom.style import timeline_style
    from cutroom.tools.captions_tools import captions
    job = Job.load(job_id)
    with silenced():
        shorts_find(job_id, count=count, version=version)
    picks = json.loads((job.root / "analysis" / "shorts.json").read_text(encoding="utf-8"))["picks"]
    made = []
    for k in range(1, len(picks) + 1):
        with silenced():
            short_create(job_id, pick_=k)
        child_id = f"{job.id}.short-{_next_number(job) - 1:02d}"
        child = Job.load(child_id)
        style = timeline_style(store.load(child))
        sc = style.get("shorts", {})
        with silenced():
            captions(child_id, style=sc.get("caption_style", "bold-karaoke"), output="youtube_short")
        first = picks[k - 1]
        hook = sc.get("hook", {})
        sent = first["sentences"][0] if first["sentences"] else None
        if sent is not None:
            with silenced():
                short_hook(child_id, text=_hook_text(first["first_line"]), fact=[f"sentences:{sent}"],
                           duration=hook.get("duration_s", 2.6))
        rep = None
        if render:
            rep = do_render(child, store.load(child), mode="preview")
        made.append({"job": child_id, "duration": round(store.load(child).duration, 1), "first_line": first["first_line"],
                     "render": rep["path"] if rep else None, "checks_ok": all(rep["checks"].values()) if rep else None})
        if not as_json:
            out.print(f"{child_id}: {made[-1]['duration']}s “{first['first_line'][:70]}”"
                      + (f" → {rep['path']} ({'OK' if made[-1]['checks_ok'] else 'CHECK FAILED'})" if rep else ""),
                      highlight=False)
    emit(as_json, {"shorts": made})


@tool(ToolManifest("short-publish", "Title, description and hashtags for a Short: agent task and check.", "shorts",
                   inputs=["Short job timeline", "words.json"], outputs=["publish.task.md", "publish.json (checked)"],
                   tests=TESTS))
def short_publish(job_id: JobArg, check: Annotated[bool, typer.Option("--check", help="Validate publish.json.")] = False,
                  as_json: JsonOpt = False) -> None:
    """Writes publish.task.md (the Short's transcript and YouTube rules) for the agent to write
    publish.json; with --check, validates it: title ≤ 100 characters, description ≤ 5,000, 3–5
    hashtags including #Shorts, every number in title/description said in the Short, no
    clickbait words the Short does not deliver, credit line present when the brief asks for one."""
    from cutroom.shorts.publish import check_publish, write_task

    job = Job.load(job_id)
    if not check:
        p = write_task(job)
        emit(as_json, {"task": str(p.relative_to(job.root))})
        if not as_json:
            out.print(f"Wrote {p.relative_to(job.root)}; write publish.json, then `cutroom short-publish {job.id} --check`.")
        return
    res = check_publish(job)
    emit(as_json, res)
    if not as_json:
        out.print(("[green]publish.json OK[/]" if res["ok"] else "[red]publish.json has problems:[/]"))
        for p in res["problems"]:
            out.print(f"  - {p}")
    if not res["ok"]:
        raise typer.Exit(1)


@tool(ToolManifest("short-hook", "Open a Short on its hook: headline in the speaker's words, never over the face.",
                   "shorts", inputs=["Short job timeline", "faces.json"], outputs=["timeline vN+1"], tests=TESTS,
                   changes_timeline=True))
def short_hook(job_id: JobArg,
               text: Annotated[str, typer.Option(help="Headline: the speaker's own words (≤ 48 characters).")],
               fact: Annotated[list[str], typer.Option("--fact", "-f", help="Where it is said, e.g. sentences:10.")],
               duration: Annotated[Optional[float], typer.Option(help="Seconds (default style shorts.hook.duration_s).")] = None,
               layout: Annotated[str, typer.Option(help="auto, fill (frame in the middle, headline above it) or crop.")] = "auto",
               version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Shorts are decided in the first second: show the hook as a headline while it is said. With
    the face-tracking crop the headline would often cover the face, so by default (auto) the
    opening switches to the blurred-fill layout (the 16:9 frame in the middle, the headline in the
    blurred band above it) when no band clears the face, then cuts to the crop."""
    from cutroom.shorts.reframe import FILL, apply_framing, crop_half_width, plan_clip_shots
    from cutroom.timeline.model import Effect
    from cutroom.timeline.ops import pack, split_at
    from cutroom.tools._common import EditContext, open_edit
    from cutroom.tools.graphics_tools import _save, build_graphic, get_template

    ctx = open_edit(job_id, version)
    tl = ctx.tl.model_copy(deep=True)
    sc = ctx.style.get("shorts", {})
    dur = duration or sc.get("hook", {}).get("duration_s", 2.6)
    spec = get_template(sc.get("hook", {}).get("template", "headline"))
    work = EditContext(ctx.job, tl, ctx.base, ctx.words, ctx.style, ctx.envs)
    g, warnings = build_graphic(work, spec, start=0.0, duration=dur, variant=None, params={"text": text}, refs=fact,
                                anchor=None, reason="hook headline", force=True)
    cover = g.fact_check.get("placement", {}).get("face_cover", 0)
    use_fill = layout == "fill" or (layout == "auto" and cover > 0.02)
    if use_fill:
        first = tl.track_clips()[0]
        cut_src = first.to_source(min(dur, first.end))
        wf = ctx.words.get(first.source)
        if wf:   # switch layouts between words, at the gap nearest the hook's end
            gaps = [(w.end + n.start) / 2 for w, n in zip(wf.words, wf.words[1:])
                    if n.start - w.end >= 0.08 and first.src_in + 1.0 < (w.end + n.start) / 2 < first.src_out - 0.2]
            if gaps:
                cut_src = min(gaps, key=lambda g: abs(g - cut_src))
                dur = round(first.to_timeline(cut_src), 3)
        split_at(tl, first.source, cut_src)
        pack(tl)
        faces = json.loads((ctx.job.root / "analysis" / first.source / "faces.json").read_text(encoding="utf-8")) \
            if (ctx.job.root / "analysis" / first.source / "faces.json").is_file() else []
        vr = ctx.job.root / "analysis" / first.source / "visual_report.json"
        shots = [x["start"] for x in json.loads(vr.read_text(encoding="utf-8")).get("shots", [])[1:]] if vr.is_file() else []
        src = tl.source(first.source)
        half = crop_half_width(src.width or 1920, src.height or 1080, tl.meta.width, tl.meta.height)
        for c in tl.track_clips():
            if c.start < dur - 1e-3:
                c.reframe = []
                c.effects = [e for e in c.effects if e.name != FILL] + [Effect(name=FILL, params={"reason": "hook layout"})]
            elif c.source == first.source and c.reframe and abs(c.src_in - cut_src) < 1e-3:
                apply_framing(c, plan_clip_shots(faces, c.src_in, c.src_out, half, sc.get("reframe", {}), shots))
        g, warnings = build_graphic(work, spec, start=0.0, duration=max(dur, 1.0), variant=None, params={"text": text},
                                    refs=fact, anchor=None, reason="hook headline (fill layout)", force=True)
    tl.graphics = [x for x in tl.graphics if x.reason.split(" (")[0] != "hook headline"] + [g]
    summary = (f"hook headline “{text}” {dur:.1f}s" + (" over the blurred-fill opening" if use_fill else "")
               + f"; source {', '.join(g.facts)}")
    n = _save(ctx, tl, "short-hook", summary)
    emit(as_json, {"version": n, "layout": "fill" if use_fill else "crop", "graphic": g.id, "warnings": warnings})
    if not as_json:
        out.print(f"{summary} → v{n:03d}", highlight=False)
        for w in warnings:
            out.print(f"[yellow]  ! {w}[/]")
