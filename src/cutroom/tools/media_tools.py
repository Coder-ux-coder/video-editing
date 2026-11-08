"""Library, sound, b-roll and thumbnail commands: assets, asset-add, asset-fetch, music, sfx,
broll-suggest, broll-add, thumbnails."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Optional

import typer

from cutroom.cli_common import JobArg, JsonOpt, VersionOpt, emit, out
from cutroom.errors import RefusedError
from cutroom.perception.textnorm import normalize
from cutroom.timeline import store
from cutroom.timeline.model import AudioBed, BRoll, Timeline
from cutroom.timeline.ops import source_to_timeline
from cutroom.timeline.outline import tc
from cutroom.tools.registry import ToolManifest, tool

TESTS = ["tests/test_media.py"]
STOP = {"this", "that", "these", "those", "with", "from", "have", "were", "they", "their", "there", "what", "when",
        "where", "which", "would", "could", "about", "into", "just", "like", "know", "really", "think", "people",
        "thing", "things", "going", "here", "some", "very", "been", "also", "then", "them", "because", "say", "said"}


def _attach(tl: Timeline, t: float) -> dict | None:
    for c in tl.track_clips():
        if c.start - 1e-6 <= t < c.end:
            src_t = round(c.to_source(t), 4)
            at = source_to_timeline(tl, c.source, src_t)
            return {"source": c.source, "t": src_t, "offset": round(t - at, 4) if at is not None else 0.0}
    return None


def _save(ctx, tl: Timeline, command: str, summary: str) -> int:
    ctx.job.log.section(f"{command} (from v{ctx.base:03d})")
    return store.save_new(ctx.job, tl, command=command, summary=summary, parent=ctx.base)


# --------------------------------------------------------------------------- library


@tool(ToolManifest("assets", "List the licence-tracked asset library (b-roll, music, sound effects).", "media",
                   inputs=["assets/library"], outputs=["asset list"], tests=TESTS))
def assets(kind: Annotated[Optional[str], typer.Option(help="image, video, music or sfx.")] = None,
           as_json: JsonOpt = False) -> None:
    """Every asset with its licence, author and tags. Only public domain, CC0 and CC BY are ever accepted."""
    from cutroom.assets.library import list_assets

    items = list_assets(kind)
    emit(as_json, {"assets": [a.__dict__ for a in items]})
    if not as_json:
        for a in items:
            out.print(f"{a.id}  {a.license}  {a.title[:50]}  by {a.author[:30] or '-'}  [{', '.join(a.tags[:6])}]",
                      markup=False, highlight=False)
        if not items:
            out.print("The library is empty: `cutroom asset-fetch --query …`, `asset-add`, or `music --generate`.")


@tool(ToolManifest("asset-add", "Add a file you are allowed to use to the library, with its licence.", "media",
                   inputs=["a media file"], outputs=["assets/library/<kind>/<id>.* + licence sidecar"], tests=TESTS))
def asset_add(path: Annotated[Path, typer.Argument(help="File to add.")],
              kind: Annotated[str, typer.Option(help="image, video, music or sfx.")],
              license: Annotated[str, typer.Option(help="public domain, CC0, or CC BY 4.0 (others are refused).")],
              author: Annotated[str, typer.Option(help="Author (required for CC BY).")] = "",
              title: Annotated[str, typer.Option(help="Title.")] = "",
              source_url: Annotated[str, typer.Option(help="Where it came from.")] = "",
              tags: Annotated[Optional[list[str]], typer.Option("--tag", help="Keywords for b-roll matching (repeat).")] = None,
              as_json: JsonOpt = False) -> None:
    """Copy a file into the library with a licence record. Client-provided media belong in the job's
    inputs/ folder instead (they are not stock)."""
    from cutroom.assets.library import add_file

    a = add_file(path, kind=kind, license=license, author=author, title=title or path.stem, source_url=source_url,
                 tags=tags or [])
    emit(as_json, a.__dict__)
    if not as_json:
        out.print(f"Added {a.id} ({a.license})")


@tool(ToolManifest("asset-fetch", "Search Wikimedia Commons for free-licensed stills and download chosen ones.",
                   "media", inputs=["network: commons.wikimedia.org"], outputs=["assets/library/image/*"], tests=TESTS))
def asset_fetch(query: Annotated[str, typer.Option(help="Search words, e.g. 'White Sands fossil footprints'.")],
                limit: Annotated[int, typer.Option(help="How many results to list.")] = 8,
                pick: Annotated[Optional[str], typer.Option(help="Download these result numbers, e.g. 1,3,4 (or 'all').")] = None,
                tags: Annotated[Optional[list[str]], typer.Option("--tag", help="Extra keywords for matching (repeat).")] = None,
                as_json: JsonOpt = False) -> None:
    """Lists Commons files whose licence (read from Commons' own metadata) is public domain, CC0
    or CC BY; share-alike, non-commercial and no-derivatives files are skipped. Only free assets
    are downloaded; nothing is ever uploaded."""
    from cutroom.assets.library import commons_fetch, commons_search
    from cutroom.paths import project_root

    results = commons_search(query, limit=limit)
    if not pick:
        emit(as_json, {"results": results})
        if not as_json:
            for k, r in enumerate(results, 1):
                out.print(f"{k}. {r['license']}  {r['title'][:70]}  by {r['author'][:40]}  {r['width']}x{r['height']}",
                          markup=False, highlight=False)
            out.print("Download with --pick 1,3 (look at the pages first: the image must fit the story).")
        return
    idx = range(1, len(results) + 1) if pick == "all" else [int(x) for x in pick.split(",")]
    got = []
    for k in idx:
        if not 1 <= k <= len(results):
            raise RefusedError(f"No result {k}.", fix=f"1–{len(results)}.")
        a = commons_fetch(results[k - 1], [t.lower() for t in (tags or [])] + normalize(query).split(),
                          project_root() / ".cache" / "fetch")
        got.append(a.__dict__)
        if not as_json:
            out.print(f"Added {a.id} ({a.license}, {a.author[:40]})", markup=False, highlight=False)
    emit(as_json, {"added": got})


# --------------------------------------------------------------------------- music and effects


@tool(ToolManifest("music", "Put a music bed under part or all of the edit (generated or from the library).",
                   "media", inputs=["timeline HEAD", "assets/library"], outputs=["timeline vN+1"], tests=TESTS,
                   changes_timeline=True))
def music(job_id: JobArg,
          generate: Annotated[Optional[str], typer.Option(help="Generate a bed: calm, documentary, upbeat or warm.")] = None,
          asset: Annotated[Optional[str], typer.Option(help="A music asset id from the library.")] = None,
          start: Annotated[Optional[float], typer.Option("--from", help="Timeline start (s).")] = None,
          end: Annotated[Optional[float], typer.Option("--to", help="Timeline end (s).")] = None,
          whole: Annotated[bool, typer.Option(help="Under the whole video.")] = False,
          gain: Annotated[float, typer.Option(help="Trim (dB) relative to the planned level.")] = 0.0,
          duck: Annotated[bool, typer.Option("--duck/--no-duck", help="Dip under speech.")] = True,
          version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Music sits 12 dB under the measured speech level in pauses and dips a further 8 dB while
    anyone speaks (style audio.music), fading in and out; the render mixes it under the processed
    voice before mastering and reports the measured speech-to-music ratio."""
    from cutroom.assets.library import add_file, get_asset
    from cutroom.assets.synth import music_bed, write
    from cutroom.paths import project_root
    from cutroom.tools._common import open_edit

    ctx = open_edit(job_id, version, need_words=False)
    tl = ctx.tl.model_copy(deep=True)
    if whole:
        start, end = 0.0, tl.duration
    if start is None or end is None or end <= start:
        raise RefusedError("Say where the music goes.", fix="--from S --to E, or --whole.")
    end = min(end, tl.duration)
    if generate:
        y = music_bed(generate, seconds=min(120.0, end - start + 4.0))
        tmp = write(project_root() / ".cache" / "gen" / f"music-{generate}.wav", y)
        a = add_file(tmp, kind="music", license="generated", title=f"Cutroom {generate} bed",
                     tags=["generated", generate], move=True)
    elif asset:
        a = get_asset(asset)
        if a.kind != "music":
            raise RefusedError(f"{asset} is a {a.kind}, not music.", fix="See `cutroom assets --kind music`.")
    else:
        raise RefusedError("Generate a bed or choose a library asset.", fix="--generate calm, or --asset ID.")
    fade = ctx.style.get("audio", {}).get("music", {}).get("fade_s", 1.5)
    bid = tl.next_id("m", [b.id for b in tl.audio.music])
    tl.audio.music.append(AudioBed(id=bid, kind="music", asset=a.id, start=round(start, 3), end=round(end, 3),
                                   gain_db=gain, fade_in=fade, fade_out=fade, duck=duck, whole=whole,
                                   attach=None if whole else _attach(tl, start), license=a.license, credit=a.credit,
                                   reason=f"music bed ({generate or a.title})"))
    n = _save(ctx, tl, "music", f"music {bid} {a.id} {tc(start)}–{tc(end)}" + (" (ducked)" if duck else ""))
    emit(as_json, {"version": n, "bed": bid, "asset": a.id})
    if not as_json:
        out.print(f"music {bid}: {a.id} {tc(start)}–{tc(end)} → v{n:03d}")


@tool(ToolManifest("sfx", "Add a sound effect at a moment, or on every overlay transition.", "media",
                   inputs=["timeline HEAD"], outputs=["timeline vN+1"], tests=TESTS, changes_timeline=True))
def sfx(job_id: JobArg, kind: Annotated[str, typer.Option(help="whoosh, riser, impact, pop or ding.")] = "whoosh",
        at: Annotated[Optional[float], typer.Option(help="Timeline time (s).")] = None,
        transitions: Annotated[bool, typer.Option("--transitions", help="One on every overlay transition.")] = False,
        gain: Annotated[float, typer.Option(help="Trim (dB) relative to 8 dB under speech.")] = 0.0,
        version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Sound effects are generated (no licence questions) and used sparingly: a whoosh on a
    transition, a pop on a list item. A whoosh on every cut is an amateur tell."""
    from cutroom.assets.library import add_file
    from cutroom.assets.synth import SFX_KINDS, SR, sfx as make_sfx, write
    from cutroom.paths import project_root
    from cutroom.tools._common import open_edit

    if kind not in SFX_KINDS:
        raise RefusedError(f"Unknown effect '{kind}'.", fix=f"Use one of {', '.join(SFX_KINDS)}.")
    ctx = open_edit(job_id, version, need_words=False)
    tl = ctx.tl.model_copy(deep=True)
    y = make_sfx(kind)
    a = add_file(write(project_root() / ".cache" / "gen" / f"sfx-{kind}.wav", y), kind="sfx", license="generated",
                 title=f"Cutroom {kind}", tags=["generated", kind], move=True)
    length = len(y) / SR
    times = []
    if transitions:
        times = [((g.start + g.end) / 2) - (length / 2 if kind == "whoosh" else 0) for g in tl.graphics
                 if g.template == "transition"]
    elif at is not None:
        times = [at]
    if not times:
        raise RefusedError("No moment given.", fix="--at S, or --transitions (needs overlay transitions).")
    ids = []
    for t in times:
        bid = tl.next_id("fx", [b.id for b in tl.audio.sfx])
        t = max(0.0, t)
        tl.audio.sfx.append(AudioBed(id=bid, kind="sfx", asset=a.id, start=round(t, 3), end=round(min(tl.duration, t + length), 3),
                                     gain_db=gain, duck=False, attach=_attach(tl, t), license=a.license,
                                     reason=f"{kind} sound effect"))
        ids.append(bid)
    n = _save(ctx, tl, "sfx", f"sfx {kind} ×{len(ids)} at " + ", ".join(tc(t) for t in times))
    emit(as_json, {"version": n, "sfx": ids})
    if not as_json:
        out.print(f"{kind} ×{len(ids)} → v{n:03d}")


# --------------------------------------------------------------------------- b-roll


def suggest_broll(tl: Timeline, words: dict, assets_: list, top: int = 10) -> list[dict]:
    """Sentences whose content words match an image/video asset's tags or title."""
    from cutroom.shorts.find import edit_sentences

    sents = edit_sentences(tl, words)
    pool = [a for a in assets_ if a.kind in ("image", "video")]
    out = []
    for s in sents:
        toks = {w for w in normalize(s.text).split() if len(w) > 3 and w not in STOP}
        toks |= {w.rstrip("s") for w in toks}
        for a in pool:
            keys = set(a.tags) | set(normalize(a.title).split())
            keys |= {k.rstrip("s") for k in keys}
            hit = sorted(toks & keys)
            if len(hit) >= 1:
                t = source_to_timeline(tl, s.source, s.start)
                if t is None:
                    continue
                out.append({"asset": a.id, "title": a.title, "license": a.license, "sentence": s.index,
                            "at": round(t + 0.4, 2), "text": s.text[:120], "matches": hit, "score": len(hit)})
    out.sort(key=lambda d: (-d["score"], d["at"]))
    seen_assets, seen_sent, picked = set(), set(), []
    for d in out:
        if d["asset"] in seen_assets or d["sentence"] in seen_sent:
            continue
        picked.append(d)
        seen_assets.add(d["asset"])
        seen_sent.add(d["sentence"])
        if len(picked) == top:
            break
    return sorted(picked, key=lambda d: d["at"])


@tool(ToolManifest("broll-suggest", "Match what is said to library stills/clips: where b-roll could illustrate.",
                   "media", inputs=["timeline HEAD", "words.json", "assets/library"], outputs=["suggestions"],
                   tests=TESTS))
def broll_suggest(job_id: JobArg, top: Annotated[int, typer.Option(help="How many.")] = 10,
                  version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Suggestions only: look at each asset and the moment before adding (b-roll must show what is
    being said, never contradict it)."""
    from cutroom.assets.library import list_assets
    from cutroom.tools._common import open_edit

    ctx = open_edit(job_id, version)
    sug = suggest_broll(ctx.tl, ctx.words, list_assets(), top)
    emit(as_json, {"suggestions": sug})
    if not as_json:
        for d in sug:
            out.print(f"{tc(d['at'])} {d['asset']} ({', '.join(d['matches'])}): “{d['text'][:80]}”", markup=False,
                      highlight=False)
        if not sug:
            out.print("No matches: fetch or add assets with tags that describe them.")


@tool(ToolManifest("broll-add", "Cut away to a library still or clip over the speaker (audio continues).", "media",
                   inputs=["timeline HEAD", "assets/library"], outputs=["timeline vN+1"], tests=TESTS,
                   changes_timeline=True))
def broll_add(job_id: JobArg, asset: Annotated[str, typer.Argument(help="Asset id.")],
              at: Annotated[float, typer.Option(help="Timeline time it starts (s).")],
              duration: Annotated[float, typer.Option(help="Seconds (2-5 reads well).")] = 3.5,
              move: Annotated[str, typer.Option(help="Ken Burns: in, out, left, right or none.")] = "in",
              src_in: Annotated[float, typer.Option(help="Video: offset into the clip (s).")] = 0.0,
              reason: Annotated[str, typer.Option(help="What it shows and why here.")] = "",
              version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """The picture cuts to the asset while the speech continues; stills move slowly (Ken Burns).
    Its licence and credit are stored in the timeline (credits go in the description)."""
    from cutroom.assets.library import get_asset
    from cutroom.tools._common import open_edit

    a = get_asset(asset)
    if a.kind not in ("image", "video"):
        raise RefusedError(f"{asset} is {a.kind}, not a picture.", fix="Use an image or video asset.")
    ctx = open_edit(job_id, version, need_words=False)
    tl = ctx.tl.model_copy(deep=True)
    if not 0 <= at < tl.duration:
        raise RefusedError(f"{tc(at)} is outside the edit.", fix="Check the time.")
    if a.width and a.height:   # never upscale a small picture into mush
        need = 0.6 * tl.meta.width
        cover = max(tl.meta.width / a.width, tl.meta.height / a.height) * 1.1
        if a.width < need or cover > 1.8:
            raise RefusedError(f"{a.id} is {a.width}x{a.height}: too small for {tl.meta.width}x{tl.meta.height} "
                               f"(would be enlarged {cover:.1f}x).", fix="Use a larger version of the picture.")
    end = min(tl.duration, at + duration)
    if a.kind == "video" and a.duration and src_in + (end - at) > a.duration + 0.01:
        raise RefusedError(f"The clip is {a.duration:.1f}s long.", fix="Shorten --duration or lower --src-in.")
    clash = [b.id for b in tl.broll if b.start < end and b.end > at]
    if clash:
        raise RefusedError(f"Overlaps b-roll {', '.join(clash)}.", fix="Move it.")
    motions = {"in": (1.0, 1.10, 0.5, 0.5, 0.5, 0.5), "out": (1.10, 1.0, 0.5, 0.5, 0.5, 0.5),
               "left": (1.08, 1.08, 0.56, 0.5, 0.44, 0.5), "right": (1.08, 1.08, 0.44, 0.5, 0.56, 0.5),
               "none": (1.0, 1.0, 0.5, 0.5, 0.5, 0.5)}
    if move not in motions:
        raise RefusedError(f"Unknown move '{move}'.", fix=f"One of {', '.join(motions)}.")
    s0, s1, x0, y0, x1, y1 = motions[move]
    bid = tl.next_id("b", [b.id for b in tl.broll])
    tl.broll.append(BRoll(id=bid, asset=a.id, kind=a.kind, start=round(at, 3), end=round(end, 3), src_in=src_in,
                          motion={"scale0": s0, "scale1": s1, "x0": x0, "y0": y0, "x1": x1, "y1": y1},
                          attach=_attach(tl, at), license=a.license, credit=a.credit, reason=reason))
    n = _save(ctx, tl, "broll-add", f"b-roll {bid} {a.id} at {tc(at)} for {end - at:.1f}s ({a.license})")
    emit(as_json, {"version": n, "broll": bid})
    if not as_json:
        out.print(f"b-roll {bid}: {a.title[:50]} at {tc(at)}–{tc(end)} → v{n:03d}", markup=False, highlight=False)


@tool(ToolManifest("thumbnails", "Thumbnail candidates (1280x720): best face frames, title away from the face.",
                   "media", inputs=["timeline HEAD", "faces.json", "source frames", "style graphics.theme"],
                   outputs=["renders/thumbnails/*.jpg", "thumbnails.json"], tests=TESTS))
def thumbnails(job_id: JobArg, text: Annotated[str, typer.Option(help="2-5 words the video supports (numbers are checked).")],
               emphasis: Annotated[Optional[list[str]], typer.Option("--emphasis", "-e", help="Word(s) in the accent colour.")] = None,
               fact: Annotated[Optional[list[str]], typer.Option("--fact", "-f", help="Source for numbers in the text.")] = None,
               count: Annotated[int, typer.Option(help="How many candidates.")] = 3,
               pick_: Annotated[Optional[int], typer.Option("--pick", help="Choose candidate N as the video's thumbnail.")] = None,
               version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Candidates from frames where the speaker's face is large, sharp and well exposed, spread over
    the video; the title sits on the side away from the face on a gradient. Checked: 1280x720, under
    2 MB, text contrast, face clear of the text. Look at them and --pick one."""
    import shutil

    from cutroom.graphics.facts import require_facts
    from cutroom.graphics.templates import get_template
    from cutroom.thumbnails import candidate_moments, compose
    from cutroom.tools._common import open_edit
    from cutroom.tools.graphics_tools import resolve_refs

    ctx = open_edit(job_id, version)
    words = text.split()
    if not 1 <= len(words) <= 6:
        raise RefusedError("A thumbnail title is 1-6 words.", fix="Shorter: the image carries the rest.")
    refs, _ = resolve_refs(ctx, fact)
    require_facts(get_template("kinetic-text"), {"text": text}, refs)
    outdir = ctx.job.root / "renders" / "thumbnails"
    outdir.mkdir(parents=True, exist_ok=True)
    if pick_ is not None:
        data = json.loads((outdir / "thumbnails.json").read_text(encoding="utf-8"))
        chosen = data["candidates"][pick_ - 1]
        shutil.copy(chosen["path"], ctx.job.root / "renders" / "thumbnail.jpg")
        data["picked"] = pick_
        (outdir / "thumbnails.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        emit(as_json, {"thumbnail": "renders/thumbnail.jpg", "from": chosen["path"]})
        if not as_json:
            out.print(f"renders/thumbnail.jpg ← candidate {pick_}")
        return
    theme = ctx.style.get("graphics", {}).get("theme", {})
    moments = candidate_moments(ctx.job, ctx.tl, count)
    cands = []
    for k, m in enumerate(moments, 1):
        res = compose(ctx.job, ctx.tl, m, text, [e.lower() for e in (emphasis or [])], outdir / f"thumb_{k}.jpg",
                      theme, outdir)
        cands.append(res)
        if not as_json:
            ok = all(res["checks"].values())
            out.print(f"{k}. {tc(m['t'])} {'OK' if ok else 'CHECK: ' + ', '.join(k2 for k2, v in res['checks'].items() if not v)}"
                      f" ({res['bytes'] // 1024} KB, contrast {res['contrast']}:1) → {Path(res['path']).relative_to(ctx.job.root)}")
    data = {"text": text, "facts": list(refs), "candidates": cands}
    (outdir / "thumbnails.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    ctx.job.log.note(f"thumbnails: {len(cands)} candidates for “{text}”")
    emit(as_json, data)


@tool(ToolManifest("media-remove", "Remove a b-roll cutaway, music bed or sound effect by id.", "media",
                   inputs=["timeline HEAD"], outputs=["timeline vN+1"], tests=TESTS, changes_timeline=True))
def media_remove(job_id: JobArg, item_id: Annotated[str, typer.Argument(help="b0001, m0001 or fx0001.")],
                 version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """To change one, remove it and add it again with the new settings (the history keeps both)."""
    from cutroom.tools._common import open_edit

    ctx = open_edit(job_id, version, need_words=False)
    tl = ctx.tl.model_copy(deep=True)
    for lst in (tl.broll, tl.audio.music, tl.audio.sfx):
        hit = next((x for x in lst if x.id == item_id), None)
        if hit:
            lst.remove(hit)
            n = _save(ctx, tl, "media-remove", f"removed {item_id} ({hit.asset})")
            emit(as_json, {"version": n, "removed": item_id})
            if not as_json:
                out.print(f"removed {item_id} → v{n:03d}")
            return
    raise RefusedError(f"No b-roll, music or effect '{item_id}'.", fix="See `cutroom show <job> --json`.")
