"""Edit operations on a timeline. Pure functions over the model; tools build on these."""

from __future__ import annotations

from dataclasses import dataclass

from cutroom.errors import JobError, RefusedError
from cutroom.job import Job, SourceRecord
from cutroom.perception.words import WordsFile, load_words
from cutroom.timeline.model import EPS, Clip, CutDecision, CutKind, Meta, Output, SourceRef, Timeline

WORD_EDGE_TOL = 0.015   # a cut this close to a word edge still counts as "on the boundary"
TOUCH_TOL = 0.045       # where two words touch (no pause), their shared edge is only approximate


@dataclass
class Removal:
    source: str
    src_in: float
    src_out: float
    kind: CutKind
    reason: str
    confidence: float | None = None
    text: str = ""


# --------------------------------------------------------------------------- sources & words


def source_ref(job: Job, s: SourceRecord) -> SourceRef:
    v = s.media.primary_video
    words = job.root / "analysis" / s.id / "words.json"
    return SourceRef(id=s.id, path=s.path, hash=s.hash, duration=s.media.duration,
                     fps=v.fps if v else None, width=v.display_width if v else None,
                     height=v.display_height if v else None, has_video=v is not None,
                     has_audio=s.media.primary_audio is not None, proxy=s.proxy,
                     audio=s.audio.get("analysis"),
                     words=f"analysis/{s.id}/words.json" if words.is_file() else None)


def words_for(job: Job, sid: str) -> WordsFile | None:
    p = job.root / "analysis" / sid / "words.json"
    return load_words(p) if p.is_file() else None


# --------------------------------------------------------------------------- assembly


def assemble(job: Job, source_ids: list[str] | None = None, *, style: str = "default") -> Timeline:
    """First timeline: each main video source, whole, in order. Nothing removed yet."""
    srcs = job.select_sources(source_ids)
    srcs = [s for s in srcs if s.media.kind == "video" and s.role in ("main", "unknown")]
    if not srcs:
        raise JobError("No main video sources to assemble.",
                       fix="Ingest footage, or pass --source for the sources to use.")
    first = srcs[0].media.primary_video
    fps = first.fps or 30.0
    # YouTube long-form: 1080p by default; keep 4K if the source is 4K (decided in render).
    w, h = (1920, 1080) if first.display_width >= first.display_height else (1080, 1920)
    tl = Timeline(meta=Meta(job_id=job.id, title=job.manifest.title, client=job.manifest.client, style=style,
                            fps=fps, width=w, height=h, aspect="16:9" if w > h else "9:16"),
                  sources=[source_ref(job, s) for s in srcs],
                  outputs=[Output(id="long", kind="youtube_long", width=w, height=h, fps=fps)])
    for k, s in enumerate(srcs, 1):
        tl.clips.append(Clip(id=f"c{k:04d}", source=s.id, src_in=0.0, src_out=round(s.media.duration, 3),
                             reason="assembly: whole source"))
    pack(tl)
    return tl


def pack(tl: Timeline, track: str = "V1") -> None:
    """Ripple: close all gaps on ``track`` by placing clips back to back in their current order."""
    t = 0.0
    for c in tl.track_clips(track):
        c.start = round(t, 6)
        t += c.duration


# --------------------------------------------------------------------------- range arithmetic


def _subtract(a: float, b: float, cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pieces = [(a, b)]
    for c0, c1 in cuts:
        nxt = []
        for p0, p1 in pieces:
            if c1 <= p0 + EPS or c0 >= p1 - EPS:
                nxt.append((p0, p1))
                continue
            if c0 > p0 + EPS:
                nxt.append((p0, c0))
            if c1 < p1 - EPS:
                nxt.append((c1, p1))
        pieces = nxt
    return pieces


def _intersect(a: float, b: float, ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(max(a, r0), min(b, r1)) for r0, r1 in ranges if min(b, r1) - max(a, r0) > EPS]


def kept_ranges(tl: Timeline, source: str, track: str = "V1") -> list[tuple[float, float]]:
    """Source ranges of ``source`` currently used on ``track``, in timeline order."""
    return [(c.src_in, c.src_out) for c in tl.track_clips(track) if c.source == source]


def _has_full_word(words: WordsFile | None, a: float, b: float) -> bool:
    if words is None:
        return True   # unknown: be conservative, never drop
    return any(w.start >= a - EPS and w.end <= b + EPS for w in words.words)


def _text(words: WordsFile | None, a: float, b: float) -> str:
    if words is None:
        return ""
    return " ".join(w.text for w in words.words if (w.start + w.end) / 2 > a and (w.start + w.end) / 2 < b)


# --------------------------------------------------------------------------- edits


def remove_ranges(tl: Timeline, removals: list[Removal], *, tool: str, version: int,
                  words: dict[str, WordsFile | None] | None = None, min_clip: float = 0.4,
                  track: str = "V1") -> list[CutDecision]:
    """Remove source ranges from every clip that uses them, then ripple.

    Locked clips are never touched (those removals are skipped). Fragments left shorter
    than ``min_clip`` that contain no complete word are removed too ("orphan").
    Returns the cut decisions actually applied.
    """
    words = words or {}
    applied: list[CutDecision] = []
    existing_ids = [c.id for c in tl.cuts]

    def new_cut(r: Removal, a: float, b: float, kind: CutKind | None = None, reason: str | None = None) -> CutDecision:
        cid = tl.next_id("x", existing_ids)
        existing_ids.append(cid)
        return CutDecision(id=cid, source=r.source, src_in=round(a, 3), src_out=round(b, 3), kind=kind or r.kind,
                           reason=reason or r.reason, confidence=r.confidence,
                           text=r.text or _text(words.get(r.source), a, b), tool=tool, version=version)

    by_source: dict[str, list[Removal]] = {}
    for r in removals:
        if r.src_out - r.src_in > EPS:
            by_source.setdefault(r.source, []).append(r)
    for sid, rs in by_source.items():
        clips = [c for c in tl.track_clips(track) if c.source == sid]
        available = sorted((c.src_in, c.src_out) for c in clips if not c.locked)
        ranges = []
        for r in sorted(rs, key=lambda r: r.src_in):
            parts = _intersect(r.src_in, r.src_out, available)
            for a, b in parts:
                if len(parts) > 1:   # split by earlier cuts: each piece names only its own words
                    r_part = Removal(r.source, a, b, r.kind, r.reason, r.confidence, _text(words.get(r.source), a, b))
                    applied.append(new_cut(r_part, a, b))
                else:
                    applied.append(new_cut(r, a, b))
                ranges.append((a, b))
            # Each range can only be removed once, even if two removals overlap.
            available = [p for a0, b0 in available for p in _subtract(a0, b0, parts)]
        if not ranges:
            continue
        ranges.sort()
        new_clips: list[Clip] = []
        for c in clips:
            if c.locked:
                continue
            pieces = _subtract(c.src_in, c.src_out, ranges)
            kept = []
            for a, b in pieces:
                if b - a < min_clip and not _has_full_word(words.get(sid), a, b):
                    applied.append(new_cut(Removal(sid, a, b, "orphan", ""), a, b, "orphan",
                                           f"fragment of {b - a:.2f}s with no complete word left between cuts"))
                    continue
                kept.append((a, b))
            tl.clips.remove(c)
            for k, (a, b) in enumerate(kept):
                cid = c.id if k == 0 else tl.next_id("c", [x.id for x in tl.clips + new_clips])
                new_clips.append(c.model_copy(update={"id": cid, "src_in": round(a, 3), "src_out": round(b, 3),
                                                      "start": c.start + (a - c.src_in) / c.speed}))
        tl.clips.extend(new_clips)
    pack(tl, track)
    tl.cuts.extend(applied)
    return applied


def restore_cut(tl: Timeline, cut_id: str, track: str = "V1") -> CutDecision:
    """Put a removed range back where it was (merging with neighbouring clips)."""
    cut = next((c for c in tl.cuts if c.id == cut_id), None)
    if cut is None:
        raise JobError(f"No cut '{cut_id}'.", fix="List cuts with `cutroom show <job> --format cuts`.")
    if cut.restored:
        raise RefusedError(f"Cut {cut_id} is already restored.", fix="Nothing to do.")
    clips = [c for c in tl.track_clips(track) if c.source == cut.source]
    left = next((c for c in clips if abs(c.src_out - cut.src_in) < 0.002), None)
    right = next((c for c in clips if abs(c.src_in - cut.src_out) < 0.002), None)
    if any(c.locked for c in (left, right) if c):
        raise RefusedError(f"Restoring {cut_id} would change a locked clip.", fix="Unlock the clip first.")
    if left and right and tl.track_clips(track).index(right) == tl.track_clips(track).index(left) + 1:
        left.src_out = right.src_out
        tl.clips.remove(right)
    elif left:
        left.src_out = cut.src_out
    elif right:
        right.src_in = cut.src_in
    else:
        before = [c for c in clips if c.src_out <= cut.src_in + EPS]
        anchor = max(before, key=lambda c: c.src_out) if before else None
        new = Clip(id=tl.next_id("c", [c.id for c in tl.clips]), source=cut.source, src_in=cut.src_in,
                   src_out=cut.src_out, reason=f"restored cut {cut_id}",
                   start=(anchor.end if anchor else 0.0) - 1e-6)
        tl.clips.append(new)
    cut.restored = True
    pack(tl, track)
    return cut


def check_cut_points(tl: Timeline, words: dict[str, WordsFile | None], track: str = "V1") -> list[str]:
    """Every clip edge must sit on a word boundary (in a gap), never inside a word."""
    problems = []
    for c in tl.track_clips(track):
        wf = words.get(c.source)
        if wf is None:
            continue
        src_dur = tl.source(c.source).duration
        for edge, t in (("in", c.src_in), ("out", c.src_out)):
            if t <= EPS or t >= src_dur - EPS:
                continue
            for k, w in enumerate(wf.words):
                if w.start + WORD_EDGE_TOL < t < w.end - WORD_EDGE_TOL:
                    prev_touch = k > 0 and w.start - wf.words[k - 1].end < 0.02
                    next_touch = k + 1 < len(wf.words) and wf.words[k + 1].start - w.end < 0.02
                    if (prev_touch and t - w.start <= TOUCH_TOL) or (next_touch and w.end - t <= TOUCH_TOL):
                        continue   # valley-snapped cut between two touching words
                    problems.append(f"clip {c.id} {edge}-point {t:.3f}s cuts inside the word "
                                    f"'{w.text}' ({w.start:.3f}-{w.end:.3f})")
                    break
    return problems


def split_at(tl: Timeline, source: str, t: float, track: str = "V1") -> None:
    """Split the clip of ``source`` that contains source time ``t`` (no-op at an edge)."""
    for c in tl.track_clips(track):
        if c.source == source and c.src_in + EPS < t < c.src_out - EPS:
            if c.locked:
                raise RefusedError(f"Clip {c.id} is locked.", fix="Unlock it first.")
            right = c.model_copy(update={"id": tl.next_id("c", [x.id for x in tl.clips]), "src_in": round(t, 3),
                                         "start": c.to_timeline(t)})
            c.src_out = round(t, 3)
            tl.clips.append(right)
            return


def layout(tl: Timeline, ordered: list[Clip]) -> None:
    """Place clips back to back in exactly this order."""
    t = 0.0
    for c in ordered:
        c.start = round(t, 6)
        t += c.duration


def move_range(tl: Timeline, source: str, a: float, b: float, to: float, reason: str,
               track: str = "V1") -> tuple[list[Clip], float]:
    """Lift source range [a, b) out of the edit and insert it at timeline time ``to``
    (snapped to the nearest clip boundary). Returns the moved clips and the actual position."""
    split_at(tl, source, a, track)
    split_at(tl, source, b, track)
    order = tl.track_clips(track)
    moving = [c for c in order if c.source == source and c.src_in >= a - EPS and c.src_out <= b + EPS]
    if not moving:
        raise JobError(f"Nothing of {source} {a:.3f}-{b:.3f} is in the edit.", fix="Check the range with `cutroom show`.")
    rest = [c for c in order if c not in moving]
    boundaries = [0.0]
    t = 0.0
    for c in rest:
        t += c.duration
        boundaries.append(t)
    k = min(range(len(boundaries)), key=lambda i: abs(boundaries[i] - to))
    for c in moving:
        c.reason = (c.reason + "; " if c.reason else "") + f"moved: {reason}"
    layout(tl, rest[:k] + moving + rest[k:])
    return moving, boundaries[k]
