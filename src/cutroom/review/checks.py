"""Automatic review checks: audio, cuts, captions, graphics, video, structure.

Each check looks at the timeline, the words, the analysis and (when given) the render report and
the rendered file, and returns issues with a severity, the evidence, and a fix: a concrete
cutroom command, marked ``auto`` when the fix loop may apply it without judgement.

Severities: blocker (must not ship: wrong words on screen, mid-word cut, broken file), major
(clearly visible/audible: loudness off, filler left in, graphic on a face), minor (a careful
editor would fix it), polish (taste; reported, never auto-fixed).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from cutroom.perception.textnorm import normalize
from cutroom.timeline.model import Timeline, validate_timeline
from cutroom.timeline.ops import check_cut_points, kept_ranges
from cutroom.timeline.outline import tc

SEVERITIES = ("blocker", "major", "minor", "polish")
CATEGORIES = ("audio", "cuts", "captions", "graphics", "video", "structure")


@dataclass
class Issue:
    check: str                 # stable id, e.g. "audio.loudness"
    category: str
    severity: str
    title: str
    evidence: str
    at: float | None = None    # timeline time
    fix: str | None = None     # cutroom command (with <job> placeholder)
    auto: bool = False         # the fix loop may apply it
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReviewInput:
    """Everything the checks may look at. ``render`` / ``video`` are optional (no render = fewer checks)."""
    tl: Timeline
    words: dict
    style: dict
    job_root: Path
    render: dict | None = None
    video: Path | None = None
    wav: Path | None = None
    shots: dict = field(default_factory=dict)      # source id -> source camera-change times
    topics: dict | None = None


# --------------------------------------------------------------------------- audio


def check_audio(ri: ReviewInput) -> list[Issue]:
    out: list[Issue] = []
    tl, ed = ri.tl, ri.style["editing"]
    target = ri.style.get("audio", {}).get("target_lufs", -14.0)
    ceiling = ri.style.get("audio", {}).get("true_peak_dbtp", -1.0)
    if not tl.audio.voice_chain:
        out.append(Issue("audio.no_chain", "audio", "major", "No voice chain or loudness normalisation",
                         f"The edit plays at the source level; YouTube target is {target} LUFS.",
                         fix="cutroom audio <job>", auto=True))
    if ri.render and ri.render.get("loudness"):
        lo = ri.render["loudness"]
        if lo.get("integrated_lufs") is not None and abs(lo["integrated_lufs"] - target) > 1.0 and tl.audio.voice_chain:
            out.append(Issue("audio.loudness", "audio", "major", "Loudness off target",
                             f"{lo['integrated_lufs']:.1f} LUFS measured, target {target} ±1.",
                             fix="cutroom audio <job>", auto=True))
        if lo.get("true_peak_dbtp") is not None and lo["true_peak_dbtp"] > ceiling + 0.05:
            out.append(Issue("audio.true_peak", "audio", "major", "True peak over the ceiling",
                             f"{lo['true_peak_dbtp']:.1f} dBTP, ceiling {ceiling} dBTP.",
                             fix="cutroom audio <job>", auto=True))
    if ri.render and ri.render.get("clicks"):
        for c in ri.render["clicks"][:5]:
            t = c.get("t") or c.get("time")
            out.append(Issue("audio.click", "audio", "major", "Click at a join",
                             f"Discontinuity at {tc(t) if t is not None else '?'}: {c}.", at=t,
                             fix="cutroom render <job> (join fades are 8 ms; check the clip edges there)"))
    # Pauses and fillers the planners would still remove in the current edit.
    from cutroom.timeline.planning import plan_fillers, plan_silence

    long_pauses, fillers = [], []
    for s in tl.sources:
        wf = ri.words.get(s.id)
        if not wf:
            continue
        kept = kept_ranges(tl, s.id)
        # What remove-silence / remove-fillers would really remove now: their plans applied to
        # a copy (planned ranges can lie in material already cut, or be refused as orphans).
        env, shots = _envelope(ri.job_root, s.id), ri.shots.get(s.id, [])
        # Pauses next to a source camera change are left alone: shortening those is what makes
        # flash shots (the review's flash-shot fix lengthens them on purpose).
        long_pauses += [r for r in _would_remove(tl, plan_silence(s.id, wf, kept, ed, env, shots), ri.words, ed)
                        if r.src_out - r.src_in > 0.25
                        and not any(r.src_in - 0.45 <= sh <= r.src_out + 0.45 for sh in shots)]
        fillers += _would_remove(tl, plan_fillers(s.id, wf, kept, ed, env, shots), ri.words, ed)
    if long_pauses:
        worst = max(long_pauses, key=lambda r: r.src_out - r.src_in)
        extra = worst.src_out - worst.src_in
        sev = "major" if len(long_pauses) >= 3 or extra > 1.0 else "minor"
        out.append(Issue("audio.long_pauses", "audio", sev, f"{len(long_pauses)} pause(s) longer than the style allows",
                         f"Longest: {extra:.2f} s extra at {worst.source} {tc(worst.src_in)}.",
                         at=_src_to_tl(tl, worst.source, worst.src_in), fix="cutroom remove-silence <job>", auto=True,
                         data={"count": len(long_pauses)}))
    if fillers:
        said = ", ".join(sorted({f.text or f.kind for f in fillers}))[:120]
        out.append(Issue("audio.fillers", "audio", "major" if len(fillers) >= 3 else "minor",
                         f"{len(fillers)} filler(s) left in", f"{said}", at=_src_to_tl(tl, fillers[0].source, fillers[0].src_in),
                         fix="cutroom remove-fillers <job>", auto=True, data={"count": len(fillers)}))
    out += _level_jumps(ri)
    return out


def _envelope(job_root: Path, sid: str):
    p = job_root / "analysis" / sid / "envelope.npz"
    return np.load(p)["env"] if p.is_file() else None


def _would_remove(tl: Timeline, removals: list, words: dict, ed: dict) -> list:
    """The cuts ``removals`` would really make on this edit (applied to a copy)."""
    from cutroom.timeline.ops import remove_ranges

    if not removals:
        return []
    applied = remove_ranges(tl.model_copy(deep=True), removals, tool="review", version=0, words=words,
                            min_clip=ed["min_clip"])
    return [c for c in applied if c.kind != "orphan"]


def _level_jumps(ri: ReviewInput, jump_db: float = 6.0, n: int = 15) -> list[Issue]:
    """Different takes or sources joined at different levels: median speech level of up to 15
    words on each side, only where the join brings together different sources or parts of the
    recording more than 30 s apart (within one continuous take, level changes are the speaker's
    own: sentence ends are naturally quieter)."""
    out = []
    clips = ri.tl.track_clips()
    for a, b in zip(clips, clips[1:]):
        if a.source == b.source and abs(b.src_in - a.src_out) <= 30.0:
            continue
        wa, wb = ri.words.get(a.source), ri.words.get(b.source)
        if not wa or not wb:
            continue
        before = [w.level_db for w in wa.words if a.src_in <= (w.start + w.end) / 2 < a.src_out and w.level_db is not None][-n:]
        after = [w.level_db for w in wb.words if b.src_in <= (w.start + w.end) / 2 < b.src_out and w.level_db is not None][:n]
        if len(before) < 3 or len(after) < 3:
            continue
        la, lb = float(np.median(before)), float(np.median(after))
        if abs(la - lb) > jump_db:
            out.append(Issue("audio.level_jump", "audio", "minor", "Level jump at a join",
                             f"Speech {la:.1f} dB before, {lb:.1f} dB after the cut at {tc(b.start)} "
                             f"(material {abs(b.src_in - a.src_out):.0f} s apart).", at=b.start,
                             fix="Match the levels (gain on the quieter clip) or choose another take"))
    return out[:10]


# --------------------------------------------------------------------------- cuts


def check_cuts(ri: ReviewInput) -> list[Issue]:
    out: list[Issue] = []
    tl = ri.tl
    for p in check_cut_points(tl, ri.words):
        m = re.search(r"clip (\S+)", p)
        clip = next((c for c in tl.clips if m and c.id == m.group(1)), None)
        out.append(Issue("cuts.mid_word", "cuts", "blocker", "Cut inside a word", p, at=clip.start if clip else None,
                         fix="cutroom restore <job> <cut id> then re-cut with --snap"))
    for p in validate_timeline(tl):
        if p.startswith("graphic"):
            continue
        out.append(Issue("cuts.invalid", "cuts", "blocker", "Invalid timeline", p, fix="cutroom validate <job>"))
    for f in flash_shots(ri):
        out.append(Issue("cuts.flash_shot", "cuts", "major", "Flash shot",
                         f"A {f['duration'] * 1000:.0f} ms shot at {tc(f['start'])}: our cut lands "
                         f"{f['duration']:.2f} s from the source's own camera change.", at=f["start"],
                         fix=f["fix"], auto=f["auto"], data=f))
    # Same-angle jump cuts inside a sentence with no change of framing (long-form only: in a
    # vertical Short, jump cuts are the accepted style and the crop carries the framing).
    clips = tl.track_clips()
    undisguised = []
    for a, b in zip(clips if tl.meta.width >= tl.meta.height else [], clips[1:]):
        if a.source != b.source or b.src_in < a.src_out - 1e-3:
            continue
        wf = ri.words.get(a.source)
        if not wf:
            continue
        before = [w for w in wf.words if a.src_in <= (w.start + w.end) / 2 < a.src_out]
        if not before or before[-1].sentence_end:
            continue
        starts = ri.shots.get(a.source, [])
        same_shot = sum(s <= a.src_out - 0.04 for s in starts) == sum(s <= b.src_in + 0.04 for s in starts)
        scale = lambda c: c.reframe[0].scale if c.reframe else 1.0   # noqa: E731
        # punch-in --auto leaves clips under 0.8 s alone (a zoom that short is a jolt): not flagged
        if (same_shot and abs(scale(a) - scale(b)) < 0.02 and b.src_in - a.src_out > 0.2
                and min(a.duration, b.duration) >= 0.8):
            undisguised.append(b.start)
    if undisguised:
        out.append(Issue("cuts.jump_cuts", "cuts", "minor", f"{len(undisguised)} mid-sentence jump cut(s) not disguised",
                         "Same camera angle, same framing on both sides of a cut inside a sentence "
                         f"(first at {tc(undisguised[0])}).", at=undisguised[0], fix="cutroom punch-in <job> --auto",
                         auto=True, data={"at": undisguised[:20]}))
    # Words clipped by a clip edge (pad too tight).
    clipped = []
    for c in clips:
        wf = ri.words.get(c.source)
        if not wf:
            continue
        for w in wf.words:
            if c.src_in - 0.002 <= w.start < c.src_in + 0.015 and c.src_in > 0.05 and w.start - c.src_in < 0.015:
                clipped.append((c.start, w.text, "start"))
            if c.src_out - 0.015 < w.end <= c.src_out + 0.002 and c.src_out < tl.source(c.source).duration - 0.05:
                clipped.append((c.end, w.text, "end"))
    if clipped:
        out.append(Issue("cuts.tight_pad", "cuts", "minor", f"{len(clipped)} word edge(s) within 15 ms of a cut",
                         "; ".join(f"'{w}' {k} at {tc(t)}" for t, w, k in clipped[:5]), at=clipped[0][0],
                         fix="cutroom restore/cut with the default pads"))
    return out


def flash_shots(ri: ReviewInput, min_s: float = 0.4) -> list[dict]:
    """Very short shots: our cut landing near a source camera change (a sliver of the other
    angle), or two cuts very close together. A sliver without words can be trimmed so the cut
    lands on the camera change; otherwise the shot can be lengthened into the removed pause next
    to it when that pause holds no words."""
    tl = ri.tl
    out = []
    clips = tl.track_clips()
    cuts = tl.active_cuts()
    for c in clips:
        wf = ri.words.get(c.source)
        words = wf.words if wf else []
        starts = [s for s in ri.shots.get(c.source, []) if c.src_in + 0.01 < s < c.src_out - 0.01]
        pieces = [(c.src_in, s, "head") for s in starts[:1]] + [(s, c.src_out, "tail") for s in starts[-1:]]
        if not starts and c.duration < min_s and c is not clips[0] and c is not clips[-1]:
            pieces = [(c.src_in, c.src_out, "clip")]
        for a, b, where in pieces:
            if b - a >= min_s:
                continue
            has_word = any(w.end > a + 0.01 and w.start < b - 0.01 for w in words)
            need = min_s + 0.02 - (b - a)
            if where == "tail":
                cut = next((x for x in cuts if x.source == c.source and abs(x.src_in - c.src_out) < 0.002), None)
                ext = cut is not None and cut.src_out - cut.src_in >= need and not any(
                    w.end > c.src_out + 0.01 and w.start < c.src_out + need for w in words)
            elif where == "head":
                cut = next((x for x in cuts if x.source == c.source and abs(x.src_out - c.src_in) < 0.002), None)
                ext = cut is not None and cut.src_out - cut.src_in >= need and not any(
                    w.start < c.src_in - 0.01 and w.end > c.src_in - need for w in words)
            else:   # a whole short clip: lengthen whichever edge borders a word-free removed pause
                ext = False
                for edge in ("out", "in"):
                    if edge == "out":
                        cut = next((x for x in cuts if x.source == c.source and abs(x.src_in - c.src_out) < 0.002), None)
                        ok = cut is not None and cut.src_out - cut.src_in >= need and not any(
                            w.end > c.src_out + 0.01 and w.start < c.src_out + need for w in words)
                    else:
                        cut = next((x for x in cuts if x.source == c.source and abs(x.src_out - c.src_in) < 0.002), None)
                        ok = cut is not None and cut.src_out - cut.src_in >= need and not any(
                            w.start < c.src_in - 0.01 and w.end > c.src_in - need for w in words)
                    if ok:
                        ext, where = True, "clip-" + edge
                        break
            t = max(0.0, c.to_timeline(a))
            if where in ("head", "tail") and not has_word:
                fix = (f"cutroom cut <job> --source {c.source} --from {a:.3f} --to {b:.3f} "
                       f"--reason 'flash shot: cut on the camera change'")
            elif ext:
                fix = f"lengthen clip {c.id} by {need:.2f}s into the removed pause (review --fix)"
            else:
                fix = "Move the join by hand (`cutroom restore` / `cut`) or accept it"
            out.append({"start": round(t, 3), "duration": round(b - a, 3), "clip": c.id, "where": where,
                        "source": c.source, "src_from": round(a, 3), "src_to": round(b, 3), "has_word": has_word,
                        "extendable": ext, "fix": fix, "auto": (where in ("head", "tail") and not has_word) or ext})
    return out


# --------------------------------------------------------------------------- captions


def check_captions(ri: ReviewInput) -> list[Issue]:
    tl = ri.tl
    out: list[Issue] = []
    if not tl.captions or not tl.captions.cues:
        out.append(Issue("captions.missing", "captions", "major", "No captions",
                         "YouTube viewers watch muted and caption files are a deliverable.",
                         fix="cutroom captions <job>", auto=True))
        return out
    from cutroom.captions.plan import timeline_words

    fillers = {normalize(f) for f in ri.style["editing"]["fillers_always"]}
    tw = timeline_words(tl, ri.words, fillers)
    now = [normalize(w.text) for w in tw]
    cw = [w for c in tl.captions.cues for w in c.words]
    shown = [normalize(w["text"]) for w in cw]
    data = {"style": tl.captions.style, "output": tl.captions.safe_zone, "burn_in": tl.captions.burn_in}
    drift = max((abs(a.start - b["start"]) for a, b in zip(tw, cw)), default=0.0) if now == shown else 0.0
    if now == shown and drift > 0.05:
        k = next(i for i, (a, b) in enumerate(zip(tw, cw)) if abs(a.start - b["start"]) > 0.05)
        out.append(Issue("captions.stale", "captions", "major", "Captions are timed for an earlier edit",
                         f"Same words, but timing is off by up to {drift * 1000:.0f} ms (first at '{cw[k]['text']}', "
                         f"{tc(tw[k].start)}): the edit changed after captions were planned.", at=tw[k].start,
                         fix=f"cutroom captions <job> --style {tl.captions.style}", auto=True, data=data))
    if now != shown:
        i = next((k for k, (a, b) in enumerate(zip(now, shown)) if a != b), min(len(now), len(shown)))
        out.append(Issue("captions.stale", "captions", "blocker", "Captions do not match the edit",
                         f"The edit changed after captions were planned: word {i} is "
                         f"'{now[i] if i < len(now) else '∅'}' in the edit but '{shown[i] if i < len(shown) else '∅'}' "
                         f"in the captions ({len(now)} vs {len(shown)} words).",
                         fix=f"cutroom captions <job> --style {tl.captions.style}", auto=True, data=data))
    cap = (ri.render or {}).get("captions")
    if cap:
        sz = cap.get("safe_zone", {})
        if sz.get("outside_count"):
            out.append(Issue("captions.safe_zone", "captions", "major", "Captions outside the safe zone",
                             f"{sz['outside_count']} sampled frames, e.g. {sz.get('outside_safe_zone', [])[:2]}.",
                             fix="Use a style with a smaller fontSizeRel for this client"))
        sy = cap.get("sync", {})
        from cutroom.render.engine import caption_sync_ok

        if sy.get("measured") and not caption_sync_ok(sy):
            out.append(Issue("captions.sync", "captions", "major", "Captions out of sync",
                             f"{sy['within_80ms']:.0%} within 80 ms (median {sy.get('median_ms')} ms); worst {sy.get('worst', [])[:2]}.",
                             fix="cutroom analyze <job> --force, then cutroom captions <job>"))
    st = tl.captions.stats
    if st.get("over_hard_cps"):
        out.append(Issue("captions.reading_speed", "captions", "polish", "Very fast captions",
                         f"{st['over_hard_cps']} captions over {ri.style['captions']['hard_max_cps']} chars/s "
                         "(captions follow the speech).", fix=None))
    return out


# --------------------------------------------------------------------------- graphics


def check_graphics(ri: ReviewInput) -> list[Issue]:
    from cutroom.graphics.templates import load_templates, min_hold

    tl = ri.tl
    out: list[Issue] = []
    specs = load_templates()
    rules = ri.style.get("graphics", {}).get("rules", {})
    measured = ((ri.render or {}).get("graphics") or {}).get("measured", {})
    for g in tl.graphics:
        spec = specs.get(g.template)
        if g.attach and g.attach.get("detached"):
            out.append(Issue("graphics.detached", "graphics", "major", f"Graphic {g.id} lost its moment",
                             f"{g.template} was attached to {g.attach['source']}@{g.attach['t']:.2f}s, which was cut.",
                             at=g.start, fix=f"cutroom graphic-remove <job> {g.id}", auto=True, data={"id": g.id}))
        if g.fact_check.get("unsupported_numbers"):
            out.append(Issue("graphics.unsourced", "graphics", "blocker", f"Graphic {g.id} shows unsourced numbers",
                             f"{g.fact_check['unsupported_numbers']}", at=g.start,
                             fix=f"cutroom graphic-remove <job> {g.id}", auto=True, data={"id": g.id}))
        brand = {"subscribe-cta", "social-handle", "logo-bug", "progress-bar", "transition"}
        if spec and g.template not in brand and spec.text_params and not g.facts:
            out.append(Issue("graphics.no_source", "graphics", "minor", f"Graphic {g.id} cites no source",
                             f"{g.template} shows text but has no reference (transcript span, brief or document).",
                             at=g.start, fix=f"cutroom graphic-remove <job> {g.id}, then graphic-add … --fact sentences:N"))
        if spec and not spec.persistent and not spec.at_cut and g.end - g.start + 1e-3 < min_hold(spec, g.params, rules):
            out.append(Issue("graphics.too_short", "graphics", "minor", f"Graphic {g.id} is too short to read",
                             f"{g.end - g.start:.1f} s on screen, needs {min_hold(spec, g.params, rules):.1f} s.", at=g.start,
                             fix=f"cutroom graphic-move <job> {g.id} --duration {min_hold(spec, g.params, rules):.1f}",
                             auto=True, data={"id": g.id, "duration": round(min_hold(spec, g.params, rules), 1)}))
        m = measured.get(g.id)
        if m and m.get("head_covered", 0) > 0.02:
            out.append(Issue("graphics.face", "graphics", "major", f"Graphic {g.id} covers the face",
                             f"{m['head_covered']:.0%} of the head covered (measured on drawn pixels).", at=g.start,
                             fix=f"cutroom graphic-move <job> {g.id} --replace", auto=True, data={"id": g.id}))
        if m and m.get("frames_outside_safe_area"):
            out.append(Issue("graphics.safe_area", "graphics", "major", f"Graphic {g.id} leaves the safe area",
                             f"{m['frames_outside_safe_area']} sampled frames outside.", at=g.start,
                             fix=f"cutroom graphic-move <job> {g.id} --replace", auto=True, data={"id": g.id}))
    busy = []
    for g in tl.graphics:
        if specs.get(g.template) and specs[g.template].persistent:
            continue
        n = sum(1 for o in tl.graphics if o is not g and o.start < g.end and o.end > g.start
                and not (specs.get(o.template) and specs[o.template].persistent))
        if n + 1 > rules.get("max_simultaneous", 2):
            busy.append(g.id)
    if busy:
        out.append(Issue("graphics.crowded", "graphics", "minor", "Too many graphics at once",
                         f"{', '.join(busy)} overlap beyond {rules.get('max_simultaneous', 2)} at a time.",
                         fix="cutroom graphic-remove <job> <id>"))
    return out


# --------------------------------------------------------------------------- video


def check_video(ri: ReviewInput) -> list[Issue]:
    out: list[Issue] = []
    r = ri.render or {}
    checks = r.get("checks", {})
    for k, sev, title in (("frames_ok", "blocker", "Frame count does not match the timeline"),
                          ("duration_ok", "blocker", "Duration does not match the timeline"),
                          ("no_edit_lists", "major", "MP4 has edit lists (YouTube asks for none)")):
        if k in checks and not checks[k]:
            out.append(Issue(f"video.{k}", "video", sev, title, f"render report: {r.get('path')}",
                             fix="cutroom render <job>"))
    if ri.video is not None and ri.video.is_file():
        out += _black_and_frozen(ri)
    # Analysis flags on shots that are in the edit.
    for s in ri.tl.sources:
        rep = ri.job_root / "analysis" / s.id / "visual_report.json"
        if not rep.is_file():
            continue
        shots = json.loads(rep.read_text(encoding="utf-8")).get("shots", [])
        kept = kept_ranges(ri.tl, s.id)
        for sh in shots:
            used = sum(max(0.0, min(b, sh["end"]) - max(a, sh["start"])) for a, b in kept)
            for flag in sh.get("flags", []):
                if used > 1.0 and ("exposed" in flag or "blur" in flag or "soft" in flag):
                    out.append(Issue("video.shot_quality", "video", "minor", "Image quality flag in a used shot",
                                     f"{s.id} {tc(sh['start'])}–{tc(sh['end'])}: {flag} ({used:.1f} s used).",
                                     at=_src_to_tl(ri.tl, s.id, max(sh["start"], kept[0][0] if kept else 0)),
                                     fix="Colour correction (later phase) or cut the shot"))
    return out


def _black_and_frozen(ri: ReviewInput) -> list[Issue]:
    """FFmpeg blackdetect / freezedetect on the render. Black inside a dip transition is intended."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(ri.video), "-an", "-vf",
                           "scale=320:-2,blackdetect=d=0.25:pix_th=0.08,freezedetect=n=0.002:d=1.5",
                           "-f", "null", "-"], capture_output=True, text=True, errors="replace")
    log = proc.stderr
    intended = [(g.start - 0.1, g.end + 0.1) for g in ri.tl.graphics if g.template in ("transition", "title-card", "chapter-card")]
    out = []
    t0 = (ri.render or {}).get("range") or [0.0, None]
    off = t0[0] or 0.0
    for m in re.finditer(r"black_start:([\d.]+) black_end:([\d.]+)", log):
        a, b = float(m.group(1)) + off, float(m.group(2)) + off
        if any(x <= a and b <= y for x, y in intended):
            continue
        out.append(Issue("video.black", "video", "major", "Black frames", f"{tc(a)}–{tc(b)} ({b - a:.2f} s).", at=a,
                         fix="Check the clip there (`cutroom show <job>`)"))
    starts = [float(x) + off for x in re.findall(r"freeze_start: ([\d.]+)", log)]
    ends = [float(x) + off for x in re.findall(r"freeze_end: ([\d.]+)", log)]
    for a, b in zip(starts, ends + [None] * (len(starts) - len(ends))):
        out.append(Issue("video.frozen", "video", "major", "Frozen picture",
                         f"No change for {((b or a) - a):.1f}+ s from {tc(a)}.", at=a,
                         fix="Check the source there; a frozen source needs b-roll or a cut"))
    return out[:10]


# --------------------------------------------------------------------------- structure


def check_structure(ri: ReviewInput) -> list[Issue]:
    tl = ri.tl
    out: list[Issue] = []
    clips = tl.track_clips()
    if not clips:
        return [Issue("structure.empty", "structure", "blocker", "Empty timeline", "No clips.", fix=None)]
    last = clips[-1]
    wf = ri.words.get(last.source)
    if wf:
        inside = [w for w in wf.words if last.src_in <= (w.start + w.end) / 2 < last.src_out]
        if inside and not inside[-1].sentence_end:
            out.append(Issue("structure.ending", "structure", "major", "The video ends mid-sentence",
                             f"Last words: '…{' '.join(w.text for w in inside[-6:])}'.", at=last.end - 1,
                             fix="cutroom restore <job> … or end on a complete sentence"))
    first = clips[0]
    wf = ri.words.get(first.source)
    if wf:
        opening = [w for w in wf.words if first.src_in <= (w.start + w.end) / 2 < first.src_out][:12]
        text = normalize(" ".join(w.text for w in opening))
        weak = re.match(r"^(sure|ok|okay|so|um|uh|hi|hello|alright|all right|yeah|well)\b", text)
        hooks = {t["first_sentence"] for t in (ri.topics or {}).get("topics", []) if t.get("hook_candidate")}
        if weak and not hooks & _sentence_ids_at(wf, first.src_in):
            out.append(Issue("structure.hook", "structure", "minor", "Weak opening",
                             f"The video opens with '{' '.join(w.text for w in opening[:8])}…'. Strong openings start on the hook.",
                             at=0.0, fix="cutroom move <job> --sentences N-M --to 0 (a hook candidate from topics.json)"))
    # Static stretches: no cut, camera change or graphic for a long time.
    changes = sorted({0.0, round(tl.duration, 3)} | {round(c.start, 3) for c in clips} |
                     {round(g.start, 3) for g in tl.graphics if not g.whole} |
                     {round(c.to_timeline(s), 3) for c in clips for s in ri.shots.get(c.source, [])
                      if c.src_in < s < c.src_out})
    gaps = [(a, b) for a, b in zip(changes, changes[1:]) if b - a > ri.style.get("pacing", {}).get("max_static_s", 25.0)]
    if gaps:
        a, b = max(gaps, key=lambda g: g[1] - g[0])
        out.append(Issue("structure.static", "structure", "polish", f"{len(gaps)} long stretch(es) without a visual change",
                         f"Longest {b - a:.0f} s from {tc(a)}: consider a punch-in, b-roll or a graphic.", at=a,
                         fix="cutroom punch-in <job> --clip <id> --scale 1.12 --push-to 1.18", data={"gaps": gaps[:10]}))
    low = [t for t in (ri.topics or {}).get("topics", []) if t.get("usefulness", 5) <= 1]
    for t in low:
        sid = t.get("source")
        wf = ri.words.get(sid)
        if not wf:
            continue
        from cutroom.timeline.planning import sentence_words

        try:
            ws = sentence_words(wf, t["first_sentence"], t["last_sentence"])
        except ValueError:
            continue
        kept = kept_ranges(tl, sid)
        used = sum(1 for w in ws if any(a <= (w.start + w.end) / 2 < b for a, b in kept))
        if used > len(ws) * 0.5:
            out.append(Issue("structure.low_value", "structure", "minor", f"Low-value topic kept: {t['title']}",
                             f"Rated 1 (off-topic/filler) in topics.json; {used}/{len(ws)} words still in the edit.",
                             at=_src_to_tl(tl, sid, ws[0].start),
                             fix=f"cutroom cut <job> --sentences {t['first_sentence']}-{t['last_sentence']} --reason '{t['title']}'"))
    return out


def _sentence_ids_at(wf, t: float) -> set[int]:
    from cutroom.timeline.planning import _sentences

    ids, k = set(), 0
    for i, s in enumerate(_sentences(wf.words)):
        if s[0].start <= t + 0.5 <= s[-1].end + 0.5:
            ids.add(i)
        k += 1
    return ids


def _src_to_tl(tl: Timeline, source: str, t: float) -> float | None:
    from cutroom.timeline.ops import source_to_timeline

    at = source_to_timeline(tl, source, t)
    if at is not None:
        return round(at, 3)
    later = [c for c in tl.track_clips() if c.source == source and c.src_in >= t]
    return round(later[0].start, 3) if later else None


def check_short(ri: ReviewInput) -> list[Issue]:
    """Vertical Shorts: length within the platform limit and the style's range, and the face kept
    inside the 9:16 crop."""
    tl = ri.tl
    if tl.meta.height <= tl.meta.width:
        return []
    from cutroom.shorts.reframe import FILL, output_face_mapper

    cfg = ri.style.get("shorts", {})
    out: list[Issue] = []
    d = tl.duration
    if d > cfg.get("hard_max_s", 180):
        out.append(Issue("short.too_long", "structure", "blocker", "Too long for a Short",
                         f"{d:.1f} s; YouTube Shorts are at most {cfg.get('hard_max_s', 180)} s.",
                         fix="cutroom cut <job> --sentences … to tighten"))
    elif d > cfg.get("max_s", 58) * 1.1 or d < cfg.get("min_s", 20) * 0.75:
        out.append(Issue("short.length", "structure", "minor", "Short length outside the style's range",
                         f"{d:.1f} s; style range {cfg.get('min_s', 20)}–{cfg.get('max_s', 58)} s.", fix=None))
    to_out = output_face_mapper(tl)
    faces_p = {s.id: ri.job_root / "analysis" / s.id / "faces.json" for s in tl.sources}
    cropped = []
    for c in tl.track_clips():
        if any(e.name == FILL for e in c.effects) or not faces_p[c.source].is_file():
            continue
        samples = json.loads(faces_p[c.source].read_text(encoding="utf-8"))
        shots = ri.shots.get(c.source, [])
        for smp in samples:
            if any(abs(smp["t"] - s) < 0.5 for s in shots):
                continue        # a 1 s sample this close to a camera change may show the other shot
            if c.src_in <= smp["t"] < c.src_out and smp["faces"]:
                f = to_out(c.to_timeline(smp["t"]), smp["faces"][0])
                if f and (f["x"] < -0.02 or f["x"] + f["w"] > 1.02):
                    cropped.append(c.to_timeline(smp["t"]))
    if cropped:
        out.append(Issue("short.face_cropped", "video", "major", "Face cut off by the vertical crop",
                         f"{len(cropped)} sampled moment(s), first at {tc(cropped[0])}.", at=cropped[0],
                         fix="cutroom short-create again (re-frames), or give that clip the fill layout"))
    return out


ALL_CHECKS = (check_audio, check_cuts, check_captions, check_graphics, check_video, check_structure, check_short)


def run_checks(ri: ReviewInput) -> list[Issue]:
    issues: list[Issue] = []
    for chk in ALL_CHECKS:
        issues += chk(ri)
    order = {s: i for i, s in enumerate(SEVERITIES)}
    return sorted(issues, key=lambda i: (order[i.severity], i.at if i.at is not None else 1e9))


def scorecard(issues: list[Issue]) -> dict:
    """0-10 per category (blocker −5, major −2, minor −0.5, polish −0.1) and overall; ship = no
    blockers and no majors."""
    cost = {"blocker": 5.0, "major": 2.0, "minor": 0.5, "polish": 0.1}
    cats = {c: 10.0 for c in CATEGORIES}
    for i in issues:
        cats[i.category] = max(0.0, cats[i.category] - cost[i.severity])
    counts = {s: sum(i.severity == s for i in issues) for s in SEVERITIES}
    return {"categories": {k: round(v, 1) for k, v in cats.items()},
            "overall": round(sum(cats.values()) / len(cats), 1), "counts": counts,
            "ready": counts["blocker"] == 0 and counts["major"] == 0}
