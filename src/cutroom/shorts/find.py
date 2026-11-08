"""Find Short candidates in an edit: windows of whole sentences, scored for a strong first line,
self-containment, a clean ending, length, topic coherence and substance.

Scores are evidence for the agent, not a verdict: every candidate carries its reasons, and the
agent (playbook 08) confirms the picks by reading them. Nothing here invents text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cutroom.graphics.facts import numbers_in
from cutroom.perception.textnorm import normalize
from cutroom.timeline.model import Timeline

BACKREF = {"and", "but", "so", "because", "which", "that", "this", "these", "those", "it", "they", "he", "she",
           "also", "then", "or", "um", "uh", "yeah", "well", "plus", "anyway", "like"}
PRONOUNS = {"that", "this", "these", "those", "it", "its", "they", "them", "their", "he", "she", "him", "her", "his"}
STRONG = {"most", "first", "never", "every", "always", "biggest", "oldest", "only", "surprising", "surprise",
          "surprises", "important", "significant", "secret", "mistake", "why", "how", "what", "amazing",
          "definitively", "evidence", "nobody", "everyone", "changes", "discovered", "found"}


@dataclass
class TSentence:
    text: str
    start: float          # timeline
    end: float
    words: int
    source: str
    src_start: float
    src_end: float
    index: int | None = None   # sentence number in the source (topics numbering)


@dataclass
class Candidate:
    start: float              # source time
    end: float
    sentences: list[TSentence]
    score: float = 0.0
    parts: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    kept: float | None = None  # seconds of kept material in the range (the Short's length)

    @property
    def source(self) -> str:
        return self.sentences[0].source

    @property
    def duration(self) -> float:
        return self.kept if self.kept is not None else self.end - self.start

    def as_dict(self) -> dict:
        return {"source": self.source, "start": round(self.start, 3), "end": round(self.end, 3),
                "duration": round(self.duration, 1),
                "score": round(self.score, 2), "parts": {k: round(v, 2) for k, v in self.parts.items()},
                "reasons": self.reasons, "first_line": self.sentences[0].text,
                "last_line": self.sentences[-1].text,
                "sentences": [s.index for s in self.sentences if s.index is not None],
                "text": " ".join(s.text for s in self.sentences)}


def edit_sentences(tl: Timeline, words: dict) -> list[TSentence]:
    """Kept sentences in natural speech order (source order), as Shorts are told: the long-form
    edit may have moved lines (a cold open) away from their context. ``start``/``end`` are source
    times; durations count only kept material."""
    from cutroom.timeline.ops import kept_ranges
    from cutroom.timeline.planning import _sentences

    out: list[TSentence] = []
    for s in tl.sources:
        wf = words.get(s.id)
        if not wf:
            continue
        kept = sorted(kept_ranges(tl, s.id))
        for k, sent in enumerate(_sentences(wf.words)):
            inside = [w for w in sent if any(a <= (w.start + w.end) / 2 < b for a, b in kept)]
            if len(inside) < max(1, len(sent) // 2):
                continue      # sentence (mostly) cut from the edit
            out.append(TSentence(text=" ".join(w.text for w in inside), start=inside[0].start, end=inside[-1].end,
                                 words=len(inside), source=s.id, src_start=inside[0].start, src_end=inside[-1].end,
                                 index=k))
    return out


def kept_duration(tl: Timeline, source: str, a: float, b: float) -> float:
    from cutroom.timeline.ops import kept_ranges

    return sum(max(0.0, min(b, y) - max(a, x)) for x, y in kept_ranges(tl, source))


def _base(tok: str) -> str:
    return tok.split("'")[0]          # "that's" → "that", "they're" → "they"


def _first_word(text: str) -> str:
    toks = normalize(text).split()
    return _base(toks[0]) if toks else ""


def score_window(sents: list[TSentence], topics: list[dict], cfg: dict, kept: float | None = None) -> Candidate:
    first, last = sents[0], sents[-1]
    c = Candidate(start=first.start, end=last.end, sentences=sents, kept=kept)
    parts, why = {}, []
    # Hook: the first line must make someone stop scrolling.
    fw = _first_word(first.text)
    toks = set(normalize(first.text).split())
    hook = 0.0
    opening = " ".join(normalize(first.text).split()[:2])
    if opening in ("you know", "i mean", "i think", "kind of", "sort of"):
        hook -= 0.8
        why.append(f"opens with '{opening}'")
    if fw in BACKREF:
        hook -= 2.0
        why.append(f"starts with '{fw}' (needs earlier context)")
    else:
        early = [_base(t) for t in normalize(first.text).split()[1:5]]
        refs = [t for t in early if t in PRONOUNS]
        if refs:
            hook -= 1.0
            why.append(f"'{refs[0]}' in the opening refers to something not shown")
    if first.words <= 16:
        hook += 0.8
    elif first.words > 28:
        hook -= 0.8
        why.append("long first sentence")
    strong = toks & STRONG
    if strong:
        hook += min(1.5, 0.5 * len(strong))
        why.append(f"strong opening words: {', '.join(sorted(strong))}")
    if first.text.rstrip().endswith("?"):
        hook += 0.7
        why.append("opens with a question")
    if numbers_in(first.text):
        hook += 0.6
        why.append("a number in the first line")
    parts["hook"] = hook
    # Ending: closes on a short, complete line.
    end = 0.5 if last.words <= 10 else 0.0
    if last.text.strip().endswith("?"):
        end -= 1.0
        why.append("ends on an open question")
    elif re.search(r"[.!]$", last.text.strip()):
        end += 0.5
    parts["ending"] = end
    # Length: near the target.
    target = cfg.get("target_s", 35)
    parts["length"] = -abs(c.duration - target) / target
    # Topic: one topic, preferably marked as a Short candidate and useful.
    ids = [s.index for s in sents if s.index is not None]
    tps = [t for t in topics if any(t["first_sentence"] <= i <= t["last_sentence"] for i in ids)]
    topic = 0.0
    if tps:
        if len(tps) > 1:
            topic -= 0.7 * (len(tps) - 1)
            why.append(f"spans {len(tps)} topics")
        if any(t.get("short_candidate") for t in tps):
            topic += 1.0
            why.append(f"topic marked as a Short: {tps[0]['title']}")
        topic += 0.3 * (sum(t.get("usefulness", 3) for t in tps) / len(tps) - 3)
        if any(t.get("hook_candidate") and t["first_sentence"] == first.index for t in tps):
            topic += 0.8
            why.append("starts on a hook line")
    parts["topic"] = topic
    # Substance: facts and density.
    text = " ".join(s.text for s in sents)
    parts["substance"] = 0.4 if numbers_in(text) else 0.0
    wps = sum(s.words for s in sents) / max(c.duration, 1e-3)
    if wps < 1.8:
        parts["substance"] -= 0.5
        why.append(f"slow ({wps:.1f} words/s)")
    c.parts, c.reasons = parts, why
    c.score = sum(parts.values())
    return c


def find_candidates(sents: list[TSentence], topics: list[dict], cfg: dict, tl: Timeline | None = None) -> list[Candidate]:
    lo, hi = cfg.get("min_s", 20), cfg.get("max_s", 58)
    out = []
    for i in range(len(sents)):
        for j in range(i, len(sents)):
            if sents[j].source != sents[i].source:
                break
            d = (kept_duration(tl, sents[i].source, sents[i].start, sents[j].end) if tl is not None
                 else sents[j].end - sents[i].start)
            if d > hi:
                break
            if d >= lo:
                out.append(score_window(sents[i:j + 1], topics, cfg, kept=d))
    return sorted(out, key=lambda c: -c.score)


def pick(cands: list[Candidate], n: int, max_overlap: float = 0.15) -> list[Candidate]:
    """Best ``n`` candidates that barely overlap (two Shorts should not tell the same thing)."""
    chosen: list[Candidate] = []
    for c in cands:
        if all(c.source != o.source or min(c.end, o.end) - max(c.start, o.start) <= max_overlap * min(c.duration, o.duration)
               for o in chosen):
            chosen.append(c)
        if len(chosen) == n:
            break
    return sorted(chosen, key=lambda c: (c.source, c.start))
