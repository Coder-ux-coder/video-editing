"""Derive a Short as its own job from a range of a parent edit.

The child job references the parent's media (no copy) and reuses its analysis through hard
links (copies where the filesystem cannot link), so every cutroom tool works on it unchanged:
captions in the Shorts safe zone, graphics, review, render. Its timeline starts from the
parent's cleaned edit inside the range, reframed for 9:16.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from cutroom.errors import JobError
from cutroom.job import Job
from cutroom.shorts.reframe import frame_vertical
from cutroom.style import timeline_style
from cutroom.timeline import store
from cutroom.timeline.model import Clip, Meta, Output, Timeline
from cutroom.timeline.ops import pack


def _link_tree(src: Path, dst: Path) -> None:
    for p in src.rglob("*"):
        q = dst / p.relative_to(src)
        if p.is_dir():
            q.mkdir(parents=True, exist_ok=True)
            continue
        q.parent.mkdir(parents=True, exist_ok=True)
        if q.exists():
            continue
        try:
            os.link(p, q)
        except OSError:
            shutil.copy2(p, q)


def short_job_id(parent: Job, k: int) -> str:
    return f"{parent.id}.short-{k:02d}"


def derive_short(parent: Job, version: int, source: str, t0: float, t1: float, *, k: int, title: str | None = None,
                 info: dict | None = None) -> Job:
    """Create job ``<parent>.short-NN`` from the parent's kept material of ``source`` between
    source times t0 and t1, in speech order (cuts the long-form edit made inside it stay cut)."""
    ptl = store.load(parent, version)
    style = timeline_style(ptl)
    cfg = style.get("shorts", {})
    jid = short_job_id(parent, k)
    root = parent.root.parent / jid
    if (root / "job.json").exists():
        raise JobError(f"Short job '{jid}' already exists.", fix="Pick another number, or remove that job folder first.")
    child = Job.create(jid, client=parent.manifest.client, title=title or f"{parent.manifest.title or parent.id} – Short {k}",
                       base=parent.root.parent)
    m = child.manifest
    m.parent = parent.id
    m.derived = {"parent_version": version, "source": source, "range": [round(t0, 3), round(t1, 3)], **(info or {})}
    for s in parent.manifest.sources:
        r = s.model_copy(deep=True)
        r.path = str(parent.source_path(s))
        r.copied = False
        if r.proxy:
            r.proxy = str(parent.abspath(r.proxy))
        r.audio = {k2: str(parent.abspath(v)) for k2, v in r.audio.items()}
        if r.transcript:
            r.transcript = str(parent.abspath(r.transcript))
        m.sources.append(r)
    child.save()
    _link_tree(parent.root / "analysis", child.root / "analysis")
    brief = parent.root / "brief.md"
    if brief.is_file():
        (child.root / "brief.md").write_text(brief.read_text(encoding="utf-8") +
                                             f"\n\n## Short\nDerived from {parent.id} v{version:03d}, "
                                             f"{source} {t0:.2f}–{t1:.2f} s.\n", encoding="utf-8")
    w, h = cfg.get("width", 1080), cfg.get("height", 1920)
    meta = Meta(job_id=jid, title=child.manifest.title, client=ptl.meta.client, style=ptl.meta.style,
                client_style=ptl.meta.client_style, fps=ptl.meta.fps, width=w, height=h, aspect="9:16",
                footage_type=ptl.meta.footage_type)
    sources = []
    for s in ptl.sources:
        r = s.model_copy(deep=True)
        r.path = str(parent.abspath(r.path))
        r.proxy = str(parent.abspath(r.proxy)) if r.proxy else None
        r.audio = str(parent.abspath(r.audio)) if r.audio else None
        r.words = f"analysis/{s.id}/words.json" if s.words else None
        sources.append(r)
    tl = Timeline(meta=meta, sources=sources, outputs=[Output(id="short", kind="youtube_short", width=w, height=h,
                                                               fps=ptl.meta.fps)])
    tl.audio = ptl.audio.model_copy(deep=True)
    n = 0
    for c in sorted((c for c in ptl.track_clips() if c.source == source), key=lambda c: c.src_in):
        a, b = max(t0, c.src_in), min(t1, c.src_out)
        if b - a < 0.05:
            continue
        n += 1
        tl.clips.append(Clip(id=f"c{n:04d}", source=source, src_in=round(a, 3), src_out=round(b, 3),
                             start=float(n), reason=f"from {parent.id} v{version:03d} clip {c.id}"))
    if not tl.clips:
        raise JobError(f"Nothing kept in {source} {t0:.2f}–{t1:.2f} s of v{version:03d}.", fix="Check the range.")
    pack(tl)
    faces = {}
    for s in tl.sources:
        p = child.root / "analysis" / s.id / "faces.json"
        faces[s.id] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
    sizes = {s.id: (s.width or 1920, s.height or 1080) for s in tl.sources}
    shots = {}
    for s in tl.sources:
        vr = child.root / "analysis" / s.id / "visual_report.json"
        shots[s.id] = ([x["start"] for x in json.loads(vr.read_text(encoding="utf-8")).get("shots", [])[1:]]
                       if vr.is_file() else [])
    counts = frame_vertical(tl.track_clips(), faces, sizes, w, h, cfg.get("reframe", {}), shots)
    child.log.section("derived Short")
    child.log.note(f"From {parent.id} v{version:03d}, {source} {t0:.2f}–{t1:.2f} s: {len(tl.clips)} clips; "
                   f"framing {counts}.")
    store.save_new(child, tl, command="short-create",
                   summary=f"Short from {parent.id} v{version:03d} {source} {t0:.2f}–{t1:.2f}s; 9:16 framing {counts}")
    parent.log.note(f"Short {jid} created from v{version:03d} {source} {t0:.2f}–{t1:.2f} s.")
    return child
