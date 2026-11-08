"""Corrections to transcribed words (names and terms the speech model got wrong).

Timing is kept: a corrected phrase occupies the same time as the misheard one. Corrections are
recorded in ``analysis/<src>/corrections.json`` so they are re-applied whenever words are rebuilt,
and propagated to Shorts derived from the job (they share the analysis)."""

from __future__ import annotations

import json
from pathlib import Path

from cutroom.errors import RefusedError
from cutroom.perception.textnorm import normalize
from cutroom.perception.words import EditWord, WordsFile

_PUNCT = ",.?!;:"


def find_phrase(wf: WordsFile, phrase: str) -> list[int]:
    """Indices (into wf.words) where the phrase starts, matching normalised words."""
    target = normalize(phrase).split()
    norms = [normalize(w.text) for w in wf.words]
    return [k for k in range(len(norms) - len(target) + 1) if norms[k:k + len(target)] == target]


def replace_phrase(wf: WordsFile, k: int, n: int, replacement: str) -> list[EditWord]:
    """Replace words k..k+n-1 by ``replacement`` over the same time span; keeps the trailing
    punctuation and sentence/clause marks of the last replaced word."""
    old = wf.words[k:k + n]
    tail = old[-1].text[len(old[-1].text.rstrip(_PUNCT)):]
    new_tokens = replacement.split()
    if not new_tokens:
        raise RefusedError("The replacement is empty.", fix="Give the corrected words.")
    t0, t1 = old[0].start, old[-1].end
    span = (t1 - t0) / len(new_tokens)
    out = []
    for j, tok in enumerate(new_tokens):
        last = j == len(new_tokens) - 1
        text = tok + (tail if last and not tok.endswith(tuple(_PUNCT)) else "")
        base = old[min(j, len(old) - 1)]
        out.append(base.model_copy(update={
            "text": text, "norm": normalize(text), "prob": 1.0,
            "start": round(t0 + j * span, 3) if len(new_tokens) != n else base.start,
            "end": round(t0 + (j + 1) * span, 3) if len(new_tokens) != n else base.end,
            "sentence_end": old[-1].sentence_end if last else False,
            "clause_end": old[-1].clause_end if last else False,
            "flags": [f for f in base.flags if f != "low_confidence"] + ["corrected"]}))
    wf.words[k:k + n] = out
    for i, w in enumerate(wf.words):
        w.i = i
    return out


def apply_corrections(wf: WordsFile, items: list[dict]) -> int:
    done = 0
    for it in items:
        n = len(normalize(it["find"]).split())
        hits = find_phrase(wf, it["find"])
        if it.get("at") is not None:
            hits = [h for h in hits if abs(wf.words[h].start - it["at"]) <= 2.0]
        for h in reversed(hits):
            replace_phrase(wf, h, n, it["replace"])
            done += 1
    return done


def corrections_path(analysis_dir: Path) -> Path:
    return analysis_dir / "corrections.json"


def load_corrections(analysis_dir: Path) -> list[dict]:
    p = corrections_path(analysis_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
