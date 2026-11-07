"""Edit planners: decide *what* to remove (with reasons), given words and a style.

Planners never touch the timeline; they return :class:`Removal` objects that
``ops.remove_ranges`` applies. Every removal is expressed relative to the kept words on
either side ("shorten the pause between A and B to 0.18 s", "drop 'um' and leave a natural
pause"), so planners compose in any order: a removal only takes what is still in the edit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from cutroom.perception.audio import ENV_HOP, ENV_WIN
from cutroom.perception.textnorm import normalize
from cutroom.perception.words import EditWord, Untranscribed, WordsFile
from cutroom.timeline.ops import Removal

VALLEY_RADIUS = 0.04   # where words touch, cut at the quietest point within this distance

# Words people repeat on purpose ("very very", "no no no"), grammatical doubles ("that that",
# "had had") and interjections ("oh, oh, wow"): never treated as stumbles.
INTENTIONAL_REPEATS = {"very", "really", "so", "no", "yes", "yeah", "bye", "ha", "go", "more", "again", "okay",
                       "ok", "that", "had", "is", "do", "far", "on", "over", "and", "well", "hey", "now",
                       "oh", "ah", "wow", "hmm", "right", "please", "many", "much", "long", "round", "out"}
# A repeated phrase after one of these is parallel structure ("how to A and how to B",
# "10,000 years ago or 5,000 years ago"), not a restart.
CONJUNCTIONS = {"and", "or", "but", "nor", "then", "yet", "plus", "versus", "vs", "than", "later", "also"}
RESTART_CUES = {"sorry", "again", "wait", "hold on", "let me start again", "let me try that again", "start over",
                "take two", "one more time", "let me rephrase", "scratch that", "actually", "i mean"}


@dataclass
class Item:
    """Something to drop between kept words: words, untranscribed sounds or just pause."""
    kind: str
    reason: str
    confidence: float
    words: list[EditWord] = field(default_factory=list)
    sounds: list[Untranscribed] = field(default_factory=list)


def kept_words(wf: WordsFile, kept: list[tuple[float, float]]) -> list[EditWord]:
    """Words whose midpoint lies inside the edit's kept ranges, in source order."""
    out = []
    for w in wf.words:
        mid = (w.start + w.end) / 2
        if any(a <= mid < b for a, b in kept):
            out.append(w)
    return out


def _pause_target(ed: dict, prev: EditWord | None) -> float:
    if prev is not None and prev.sentence_end:
        return ed["keep_pause_sentence_end"]
    return ed["keep_pause_mid_sentence"]


def snap_to_valley(env: np.ndarray | None, t: float, lo: float, hi: float, radius: float = VALLEY_RADIUS) -> float:
    """Move ``t`` to the quietest envelope frame within ``radius`` (and inside [lo, hi])."""
    if env is None:
        return t
    a = max(lo, t - radius)
    b = min(hi, t + radius)
    i0, i1 = int(max(0, (a - ENV_WIN / 2) / ENV_HOP)), int(min(len(env) - 1, (b - ENV_WIN / 2) / ENV_HOP))
    if i1 <= i0:
        return t
    k = i0 + int(np.argmin(env[i0:i1 + 1]))
    return round(min(max(k * ENV_HOP + ENV_WIN / 2, a), b), 3)


def _gap_removal(source: str, prev: EditWord | None, nxt: EditWord | None, keep: float, ed: dict,
                 sounds: list[Untranscribed], gap_lo: float, gap_hi: float,
                 cover: tuple[float, float] | None = None,
                 env: np.ndarray | None = None) -> tuple[float, float] | None:
    """Removal range inside (gap_lo, gap_hi) leaving about ``keep`` seconds of pause.

    ``cover`` is a span (dropped words/sounds) the removal must include entirely; the kept
    pause then comes only from real silence on either side of it. Where the removal edge
    meets a kept word with no pause, the edge moves to the quietest point nearby.
    """
    pad_a, pad_b = ed["pad_after_word"], ed["pad_before_word"]
    a = max(pad_a, keep * 0.6) if prev else 0.0
    b = max(pad_b, keep - a) if nxt else 0.0
    if prev is None:
        a = 0.0
        b = max(ed["head_pad"], pad_b)
    if nxt is None:
        b = 0.0
        a = max(ed["tail_pad"], pad_a)
    r0, r1 = gap_lo + a, gap_hi - b
    if cover is not None:
        c0, c1 = cover
        if r0 > c0:
            r0 = c0 if c0 - gap_lo > 0.02 else snap_to_valley(env, c0, gap_lo - VALLEY_RADIUS, c0 + VALLEY_RADIUS)
        if r1 < c1:
            r1 = c1 if gap_hi - c1 > 0.02 else snap_to_valley(env, c1, c1 - VALLEY_RADIUS, gap_hi + VALLEY_RADIUS)
    # Never split a sound: a sound touching an edge is taken whole into the removal if it fits
    # inside the gap, otherwise the edge moves to keep it whole.
    for s in sounds:
        if s.start < r0 < s.end:
            r0 = s.start if s.start >= gap_lo + 0.02 else s.end
        if s.start < r1 < s.end:
            r1 = s.end if s.end <= gap_hi - 0.02 else s.start
    if r1 - r0 < 0.04:
        return None
    return round(r0, 3), round(r1, 3)


def _sounds_in(wf: WordsFile, lo: float, hi: float) -> list[Untranscribed]:
    return [s for s in wf.untranscribed if s.start >= lo - 1e-3 and s.end <= hi + 1e-3]


def _neighbours(words: list[EditWord], drop: set[int], k: int) -> tuple[EditWord | None, EditWord | None]:
    """Nearest kept, non-dropped words before/after position k in ``words``."""
    p = next((words[j] for j in range(k - 1, -1, -1) if words[j].i not in drop), None)
    n = next((words[j] for j in range(k + 1, len(words)) if words[j].i not in drop), None)
    return p, n


def _range_end(kept: list[tuple[float, float]], t: float, side: str) -> float:
    for a, b in kept:
        if a - 1e-3 <= t <= b + 1e-3:
            return a if side == "lo" else b
    return t


# --------------------------------------------------------------------------- silence


def plan_silence(source: str, wf: WordsFile, kept: list[tuple[float, float]], ed: dict,
                 env: np.ndarray | None = None) -> list[Removal]:
    """Shorten every pause longer than ``max_pause``; trim head and tail of each kept stretch."""
    words = kept_words(wf, kept)
    out: list[Removal] = []
    if not words:
        return out
    # Head (before the first word) and tail (after the last word).
    first, last = words[0], words[-1]
    lo = _range_end(kept, first.start, "lo")
    r = _gap_removal(source, None, first, 0, ed, _sounds_in(wf, lo, first.start), lo, first.start)
    if r:
        out.append(Removal(source, *r, kind="head", reason="no speech before the first word", confidence=0.95))
    hi = _range_end(kept, last.end, "hi")
    r = _gap_removal(source, last, None, 0, ed, _sounds_in(wf, last.end, hi), last.end, hi)
    if r:
        out.append(Removal(source, *r, kind="tail", reason="no speech after the last word", confidence=0.95))
    for p, n in zip(words, words[1:]):
        gap = n.start - p.end
        if gap <= ed["max_pause"]:
            continue
        keep = _pause_target(ed, p)
        reason = f"{gap:.2f}s pause shortened to {keep:.2f}s"
        conf = 0.95
        # Possible dramatic pause: after a sentence, before a short punchy sentence.
        nxt_sentence = []
        for w in words[words.index(n):]:
            nxt_sentence.append(w)
            if w.sentence_end:
                break
        if p.sentence_end and gap >= ed["dramatic_pause_min"] and len(nxt_sentence) <= 6:
            keep = max(keep, ed["dramatic_pause_keep"])
            reason = f"{gap:.2f}s pause before a short sentence kept at {keep:.2f}s (possible dramatic pause)"
            conf = 0.7
        sounds = _sounds_in(wf, p.end, n.start)
        if any(s.kind == "voiced" for s in sounds):
            conf = min(conf, 0.85)
            reason += "; pause contains an untranscribed voiced sound"
        r = _gap_removal(source, p, n, keep, ed, sounds, p.end, n.start)
        if r:
            out.append(Removal(source, *r, kind="silence", reason=reason, confidence=conf))
    return out


# --------------------------------------------------------------------------- fillers


def _contextual_filler_spans(words: list[EditWord], phrases: list[str]) -> list[tuple[int, int]]:
    """Discourse markers set off by punctuation, e.g. ", you know," or "Like, …" at a clause start."""
    spans = []
    toks = [w.norm for w in words]
    for phrase in sorted(phrases, key=lambda p: -len(p.split())):
        ph = phrase.split()
        k = len(ph)
        for i in range(len(toks) - k + 1):
            if toks[i:i + k] != ph:
                continue
            before_break = i == 0 or words[i - 1].clause_end or words[i - 1].sentence_end
            after_break = words[i + k - 1].clause_end
            if before_break and after_break:
                spans.append((i, i + k))
    return spans


def plan_fillers(source: str, wf: WordsFile, kept: list[tuple[float, float]], ed: dict,
                 env: np.ndarray | None = None) -> list[Removal]:
    """Drop filler words (and untranscribed voiced sounds in pauses), leaving natural pauses."""
    words = kept_words(wf, kept)
    always = {normalize(f) for f in ed["fillers_always"]}
    drop: dict[int, str] = {}
    for w in words:
        if w.norm in always:
            drop[w.i] = "always"
    if ed.get("remove_contextual_fillers"):
        for i0, i1 in _contextual_filler_spans(words, [normalize(f) for f in ed["fillers_contextual"]]):
            for w in words[i0:i1]:
                drop.setdefault(w.i, "contextual")
    out: list[Removal] = []
    # Group consecutive dropped words into one removal between the same kept neighbours.
    k = 0
    while k < len(words):
        if words[k].i not in drop:
            k += 1
            continue
        j = k
        while j + 1 < len(words) and words[j + 1].i in drop:
            j += 1
        group = words[k:j + 1]
        p, n = _neighbours(words, set(drop), k)
        lo = p.end if p else _range_end(kept, group[0].start, "lo")
        hi = n.start if n else _range_end(kept, group[-1].end, "hi")
        sounds = _sounds_in(wf, lo, hi)
        keep = _pause_target(ed, p)
        r = _gap_removal(source, p, n, keep, ed, sounds, lo, hi, cover=(group[0].start, group[-1].end), env=env)
        if r:
            text = " ".join(w.text for w in group)
            ctx = drop[group[0].i] == "contextual"
            conf = round(min(0.97, 0.6 + 0.4 * min(w.prob for w in group)) if not ctx else 0.6, 2)
            out.append(Removal(source, *r, kind="filler",
                               reason=f"{'discourse marker' if ctx else 'filler'} '{text.strip(',.')}'",
                               confidence=conf, text=text))
        k = j + 1
    if ed.get("remove_acoustic_fillers"):
        for p, n in zip(words, words[1:]):
            for s in _sounds_in(wf, p.end, n.start):
                dur = s.end - s.start
                if s.kind != "voiced" or not (ed["acoustic_filler_min"] <= dur <= ed["acoustic_filler_max"]):
                    continue
                if any(r.src_in <= s.start and r.src_out >= s.end for r in out):
                    continue
                keep = _pause_target(ed, p)
                r = _gap_removal(source, p, n, keep, ed, [s], p.end, n.start, cover=(s.start, s.end), env=env)
                if r:
                    out.append(Removal(source, *r, kind="acoustic_filler",
                                       reason=f"untranscribed voiced sound {dur:.2f}s in a pause (likely 'um'/'uh')",
                                       confidence=round(0.5 + 0.4 * s.voicing, 2)))
    return out


# --------------------------------------------------------------------------- retakes and stumbles


def _lcs(a: list[str], b: list[str]) -> int:
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[-1]))
        prev = cur
    return prev[-1]


def _sentences(words: list[EditWord]) -> list[list[EditWord]]:
    out, cur = [], []
    for w in words:
        cur.append(w)
        if w.sentence_end:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _content(ws: list[EditWord], fillers: set[str]) -> list[str]:
    return [t for w in ws for t in w.norm.split() if t not in fillers]


@dataclass
class RetakeCandidate:
    id: str
    kind: str                  # "retake" (sentence redone), "false_start", "stumble"
    drop: list[EditWord]       # words to remove (the abandoned attempt)
    keep_from: EditWord        # first word of the kept take
    confidence: float
    reason: str

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "confidence": self.confidence, "reason": self.reason,
                "drop_text": " ".join(w.text for w in self.drop),
                "drop_from": self.drop[0].start, "drop_to": self.drop[-1].end,
                "kept_take_starts": self.keep_from.start, "kept_take_text_start": self.keep_from.text}


def detect_retakes(wf: WordsFile, kept: list[tuple[float, float]], ed: dict) -> list[RetakeCandidate]:
    """Find abandoned attempts the speaker redid: stumbles, false starts and repeated sentences.

    The kept take is normally the *last* complete one (speakers redo until satisfied); an
    earlier take wins only if the later one is clearly worse (shorter or much less clear).
    """
    words = kept_words(wf, kept)
    fillers = {normalize(f) for f in ed["fillers_always"]}
    cands: list[RetakeCandidate] = []
    used: set[int] = set()

    # 1. Sentence-level retakes.
    sents = _sentences(words)
    for ai, A in enumerate(sents):
        ta = _content(A, fillers)
        if len(ta) < 4 or any(w.i in used for w in A):
            continue
        for bi in range(ai + 1, min(len(sents), ai + 5)):
            B = sents[bi]
            if B[0].start - A[-1].end > ed["retake_window_s"]:
                break
            tb = _content(B, fillers)
            if not tb:
                continue
            common = _lcs(ta, tb)
            sim_a, sim_b = common / len(ta), common / len(tb)
            if sim_a < max(ed["retake_min_similarity"], 0.7) or sim_b < 0.4:
                continue
            # Shared opening only ("We want to know X. We want to know Y.") is parallelism.
            pre = 0
            while pre < min(len(ta), len(tb)) and ta[pre] == tb[pre]:
                pre += 1
            tail_a, tail_b = ta[pre:], tb[pre:]
            if tail_a and _lcs(tail_a, tail_b) / len(tail_a) < 0.5:
                continue
            between = [w for S in sents[ai + 1:bi] for w in S]
            between_text = normalize(" ".join(w.text for w in between))
            cue = next((c for c in RESTART_CUES if c in between_text), None)
            pause = B[0].start - A[-1].end
            mean_p = lambda ws: sum(w.prob for w in ws) / len(ws)  # noqa: E731
            later_worse = len(tb) < 0.6 * len(ta) or mean_p(B) < mean_p(A) - 0.2
            if later_worse:
                break   # keep A; B is probably a callback or summary, not a redo
            conf = 0.6 + 0.2 * (sim_a >= 0.85) + 0.15 * (pause >= 1.0 or cue is not None) \
                + 0.05 * (not A[-1].sentence_end)
            if len(between) > 6 and cue is None:
                conf -= 0.2   # dropping a lot of other speech needs a person to confirm
            if A[0].norm in CONJUNCTIONS and A[0].norm == B[0].norm:
                conf -= 0.15  # "Or X. Or Y.": alternatives in a list, probably intentional
            why = [f"sentence repeated ({sim_a:.0%} of its words reappear {pause:.1f}s later)"]
            if cue:
                why.append(f"restart cue '{cue}'")
            if between:
                why.append(f"{len(between)} words between the takes also dropped")
            why.append("keeping the later take")
            drop = A + between
            cands.append(RetakeCandidate("", "retake", drop, B[0], round(min(conf, 0.98), 2), "; ".join(why)))
            used |= {w.i for w in drop}
            break

    # 2. Immediate repetitions inside a sentence: stumbles ("we are, we are") and false starts.
    toks = [w.norm for w in words]
    contextual = {normalize(f) for f in ed.get("fillers_contextual", [])}
    i = 0
    while i < len(words):
        if words[i].i in used or toks[i] in fillers:
            i += 1
            continue
        found = None
        for k in (4, 3, 2, 1):
            # Restart may come right after (stumble) or after a few abandoned words (false start).
            for skip in range(0, 5 if k >= 2 else 1):
                j = i + k + skip
                if j + k > len(words) or any(words[x].i in used for x in range(i, j + k)):
                    continue
                if toks[i:i + k] != toks[j:j + k]:
                    continue
                if words[j].start - words[i + k - 1].end > 2.0:
                    continue
                if k == 1 and (toks[i] in INTENTIONAL_REPEATS or len(toks[i]) < 2
                               or words[j].start - words[i].end > 0.6):
                    continue
                span = words[i:j]
                if any(w.sentence_end for w in span):
                    continue      # a finished sentence is not abandoned ("...really neat. In a way...")
                if toks[i] in CONJUNCTIONS or any(t in CONJUNCTIONS for t in toks[i + 1:j]):
                    continue      # parallel structure ("and how A and how B"), not a restart
                found = (k, skip, j)
                break
            if found:
                break
        if not found:
            i += 1
            continue
        k, skip, j = found
        first = words[i:i + k]
        skipped = words[i + k:j]
        gap_after_first = (skipped[0].start if skipped else words[j].start) - first[-1].end
        break_after_first = first[-1].clause_end or gap_after_first >= 0.15
        filler_skip = all(w.norm in fillers or w.norm in contextual or
                          any(w.norm in c.split() for c in contextual) for w in skipped)
        if skip and break_after_first and not filler_skip:
            # "human tracks, fresh human tracks": the extra words start the corrected phrase.
            drop, restart = first, skipped[0]
        else:
            drop, restart = words[i:j], words[j]
        kind = "stumble" if skip == 0 else "false_start"
        phrase = " ".join(w.text for w in first).strip(",.")
        has_number = any(any(ch.isdigit() for ch in w.text) for w in drop + [restart])
        if k == 1:
            conf = 0.8 if words[j].start - words[i].end <= 0.35 else 0.6
        else:
            conf = {2: 0.85, 3: 0.9, 4: 0.92}[k] - (0.1 if skip else 0.0)
            if skip and filler_skip:
                conf += 0.05
            if skip and not filler_skip and not first[-1].clause_end and gap_after_first < 0.15:
                conf -= 0.1
        reason = (f"'{phrase}' said twice in a row; dropping the first" if skip == 0 else
                  f"restarted '{phrase}' after {skip} word(s); dropping the abandoned attempt")
        if has_number:
            conf = min(conf, 0.5)
            reason += "; involves a number, so it may be a factual correction: a person should choose"
        cands.append(RetakeCandidate("", kind, drop, restart, round(min(conf, 0.95), 2), reason))
        used |= {w.i for w in drop}
        i = j
    cands.sort(key=lambda c: c.drop[0].start)
    for n, c in enumerate(cands, 1):
        c.id = f"r{n:03d}"
    return cands


def plan_retakes(source: str, wf: WordsFile, kept: list[tuple[float, float]], ed: dict,
                 cands: list[RetakeCandidate], env: np.ndarray | None = None) -> list[Removal]:
    """Removals for the given retake candidates, leaving a natural pause at the join."""
    words = kept_words(wf, kept)
    index = {w.i: k for k, w in enumerate(words)}
    out = []
    for c in cands:
        drop_ids = {w.i for w in c.drop}
        k = index.get(c.drop[0].i)
        if k is None:
            continue
        p, _ = _neighbours(words, drop_ids, k)
        n = c.keep_from
        lo = p.end if p else _range_end(kept, c.drop[0].start, "lo")
        keep = _pause_target(ed, p)
        r = _gap_removal(source, p, n, keep, ed, _sounds_in(wf, lo, n.start), lo, n.start,
                         cover=(c.drop[0].start, c.drop[-1].end), env=env)
        if r:
            kind = "retake" if c.kind == "retake" else "stumble"
            out.append(Removal(source, *r, kind=kind, reason=f"{c.id}: {c.reason}", confidence=c.confidence,
                               text=" ".join(w.text for w in c.drop)))
    return out
