"""Edit-ready words: Whisper's words with boundaries refined against the audio.

Whisper's word times are good inside continuous speech but often wrong at pauses
(measured in Phase 0: up to ~400 ms, worst for the first word after silence). Cuts and
captions happen exactly at pauses, so this module re-places every word edge that borders
a pause onto the real energy onset/offset of the speech, using a 5 ms envelope.

It also finds voiced sounds inside pauses that have no transcribed word: these are
usually fillers ("um", "uh") or stumbles that Whisper silently dropped.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from cutroom.joblog import now_iso
from cutroom.perception.audio import (ENV_HOP, ENV_WIN, envelope_db, levels, periodicity, read_mono,
                                      runs_above)
from cutroom.perception.textnorm import normalize

GAP_FOR_REFINE = 0.08     # only edges next to a gap at least this long are re-placed
MIN_WORD = 0.04
VOICED_NCCF = 0.45
DEFAULT_FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "ahh", "hmm", "mm", "mhm"}


class EditWord(BaseModel):
    i: int
    text: str
    norm: str
    start: float
    end: float
    raw_start: float
    raw_end: float
    prob: float
    segment: int
    sentence_end: bool = False
    clause_end: bool = False
    filler: bool = False
    level_db: float | None = Field(None, description="Mean speech level over the word (dBFS).")
    flags: list[str] = Field(default_factory=list)


class Untranscribed(BaseModel):
    """Audible sound in a pause with no transcribed word."""
    start: float
    end: float
    kind: str                 # "voiced" (likely filler/stumble) or "unvoiced" (breath, noise)
    voicing: float
    level_db: float
    after_word: int | None    # index of the preceding word (None = before the first word)


class WordsFile(BaseModel):
    schema_: str = Field("cutroom.words/1", alias="schema")
    source_id: str | None = None
    created_at: str = Field(default_factory=now_iso)
    noise_floor_db: float
    speech_level_db: float
    threshold_db: float
    words: list[EditWord]
    untranscribed: list[Untranscribed]
    stats: dict

    model_config = {"populate_by_name": True}

    def text_between(self, t0: float, t1: float) -> str:
        return " ".join(w.text for w in self.words if w.end > t0 and w.start < t1)


_PUNCT_ONLY = re.compile(r"^[\W_]+$")


_CONTINUATION = re.compile(r"^(?:[,.]\d|-[A-Za-z])")


def _flatten(transcript) -> list[dict]:
    """Whisper words → dicts. Punctuation-only tokens ("–", "...") and split-off continuations
    (",000" of "10,000", "-toothed" of "saber-toothed", "-S" of "O-D-E-S-S") merge into the
    previous word, so a number or hyphenated word is one word for editing."""
    out: list[dict] = []
    for seg in transcript.segments:
        for w in seg.words:
            text = w.text.strip()
            if not text:
                continue
            if _PUNCT_ONLY.match(text) or (_CONTINUATION.match(text) and out
                                           and w.start - out[-1]["end"] < 0.3):
                if out:
                    out[-1]["text"] += text
                    if not _PUNCT_ONLY.match(text):
                        out[-1]["end"] = max(out[-1]["end"], w.end)
                        out[-1]["prob"] = min(out[-1]["prob"], w.prob)
                continue
            out.append({"text": text, "start": w.start, "end": w.end, "prob": w.prob, "segment": seg.id})
    for d in out:
        d["sentence_end"] = bool(re.search(r"[.?!][\"')\]]*$", d["text"]))
        d["clause_end"] = bool(re.search(r"[,;:–—-][\"')\]]*$", d["text"])) and not d["sentence_end"]
    return out


MIN_SILENCE = 0.04        # a pause is at least this long below the speech threshold
LATE_TOLERANCE = 0.15     # Whisper edge may sit this far inside the neighbouring pause/speech


def silence_runs(env: np.ndarray, thr: float, min_len: float = MIN_SILENCE) -> list[tuple[float, float]]:
    """(start, end) in seconds of every region below ``thr`` lasting at least ``min_len``."""
    below = runs_above(-env, -thr + 1e-6, max(1, int(min_len / ENV_HOP)))
    return [(s * ENV_HOP + ENV_WIN * 0.75, (e - 1) * ENV_HOP + ENV_WIN * 0.25) for s, e in below]


def _pause_after(sil, word_start: float, raw_end: float, limit: float) -> float | None:
    """Start of the first pause after a word: where its speech really ends."""
    for a, b in sil:
        if b <= max(raw_end - LATE_TOLERANCE, word_start + MIN_WORD):
            continue
        if a >= limit:
            return None
        return max(a, word_start + MIN_WORD)
    return None


def _pause_before(sil, raw_start: float, lower: float) -> float | None:
    """End of the last pause before a word: where its speech really starts."""
    for a, b in reversed(sil):
        if a >= raw_start + LATE_TOLERANCE:
            continue
        if b <= lower:
            return None
        return b
    return None


def refine_words(raw: list[dict], env: np.ndarray, thr: float, duration: float) -> tuple[list[dict], dict]:
    """Re-place word edges that border pauses onto the real pause boundaries.

    For every gap Whisper reports between two words (and before the first / after the
    last word), find the pause in the audio: the earlier word ends where the first silence
    after it begins, the later word starts where the last silence before it ends. Anything
    audible between those two silences (a breath, an untranscribed "um") is left outside
    both words so it can be detected and cut. Edges inside continuous speech are untouched.
    """
    sil = silence_runs(env, thr)
    words = [dict(w, raw_start=w["start"], raw_end=w["end"], flags=[]) for w in raw]
    shifts: list[float] = []
    n = len(words)

    def set_edge(w: dict, key: str, t: float | None, flag: str) -> None:
        if t is not None and abs(t - w[key]) > ENV_HOP:
            shifts.append(t - w[key])
            w[key] = t
            w["flags"].append(flag)

    for k in range(-1, n):
        left = words[k] if k >= 0 else None
        right = words[k + 1] if k + 1 < n else None
        raw_gap = (right["raw_start"] if right else duration) - (left["raw_end"] if left else 0.0)
        tiny = (left and left["raw_end"] - left["raw_start"] < MIN_WORD) or \
               (right and right["raw_end"] - right["raw_start"] < MIN_WORD)
        if left and right and raw_gap < GAP_FOR_REFINE and not tiny:
            continue
        end_t = start_t = None
        if left:
            limit = right["raw_end"] - MIN_WORD if right else duration
            end_t = _pause_after(sil, left["start"], left["raw_end"], limit)
        if right:
            lower = left["start"] + MIN_WORD if left else 0.0
            start_t = _pause_before(sil, right["raw_start"], lower)
            start_t = min(start_t, right["raw_end"] - MIN_WORD) if start_t is not None else None
        if end_t is not None and start_t is not None and end_t > start_t:
            continue   # inconsistent: no clear pause, keep Whisper's edges
        if left:
            set_edge(left, "end", end_t, "end_refined")
        if right:
            set_edge(right, "start", start_t, "start_refined")
    # Enforce order and a minimum duration without crossing neighbours.
    for k, w in enumerate(words):
        if k and w["start"] < words[k - 1]["end"]:
            w["start"] = words[k - 1]["end"]
        if w["end"] - w["start"] < MIN_WORD:
            limit = words[k + 1]["start"] if k + 1 < n else duration
            w["end"] = min(limit, w["start"] + MIN_WORD)
        w["start"], w["end"] = round(w["start"], 3), round(max(w["end"], w["start"]), 3)
    stats = {"edges_refined": len(shifts),
             "mean_abs_shift_ms": round(float(np.mean(np.abs(shifts)) * 1000), 1) if shifts else 0.0,
             "max_abs_shift_ms": round(float(np.max(np.abs(shifts)) * 1000), 1) if shifts else 0.0}
    return words, stats


def find_untranscribed(words: list[dict], env: np.ndarray, thr: float, x16: np.ndarray, sr16: int,
                       duration: float, min_len: float = 0.12) -> list[Untranscribed]:
    """Audible regions inside pauses that no word covers, classified voiced/unvoiced."""
    out = []
    bounds = [(None, 0.0, words[0]["start"] if words else duration)]
    bounds += [(k, w["end"], words[k + 1]["start"] if k + 1 < len(words) else duration) for k, w in enumerate(words)]
    for after, g0, g1 in bounds:
        if g1 - g0 < min_len + 0.04:
            continue
        a, b = int(g0 / ENV_HOP) + 2, int(g1 / ENV_HOP) - 2   # stay clear of the word edges
        if b - a < int(min_len / ENV_HOP):
            continue
        for s, e in runs_above(env[a:b], thr, int(min_len / ENV_HOP)):
            t0, t1 = (a + s) * ENV_HOP, (a + e - 1) * ENV_HOP + ENV_WIN
            seg = x16[int(t0 * sr16): int(t1 * sr16)]
            pv = periodicity(seg, sr16)
            voicing = float(np.median(pv)) if len(pv) else 0.0
            out.append(Untranscribed(start=round(t0, 3), end=round(t1, 3),
                                     kind="voiced" if voicing >= VOICED_NCCF else "unvoiced",
                                     voicing=round(voicing, 3),
                                     level_db=round(float(env[a + s: a + e].mean()), 1), after_word=after))
    return out


def build_words(transcript, analysis_wav: Path, asr_wav: Path, *, source_id: str | None = None,
                fillers: set[str] | None = None) -> tuple[WordsFile, np.ndarray]:
    """Refined words + untranscribed sounds for one source. Returns (words file, envelope)."""
    x, sr = read_mono(analysis_wav)
    env = envelope_db(x, sr)
    duration = len(x) / sr
    floor, speech, thr = levels(env)
    raw = _flatten(transcript)
    refined, stats = refine_words(raw, env, thr, duration)
    x16, sr16 = read_mono(asr_wav)
    unt = find_untranscribed(refined, env, thr, x16, sr16, duration)
    fill = {f.lower() for f in (fillers or DEFAULT_FILLERS)}
    words = []
    for k, w in enumerate(refined):
        norm = normalize(w["text"])
        flags = w["flags"] + (["low_confidence"] if w["prob"] < 0.5 else [])
        a, b = int(w["start"] / ENV_HOP), max(int(w["start"] / ENV_HOP) + 1, int(w["end"] / ENV_HOP))
        level = round(float(np.mean(env[a:b])), 1) if b <= len(env) and b > a else None
        words.append(EditWord(i=k, text=w["text"], norm=norm, start=w["start"], end=w["end"], level_db=level,
                              raw_start=round(w["raw_start"], 3), raw_end=round(w["raw_end"], 3),
                              prob=w["prob"], segment=w["segment"], sentence_end=w["sentence_end"],
                              clause_end=w["clause_end"], filler=norm in fill, flags=flags))
    stats |= {"words": len(words), "transcribed_fillers": sum(w.filler for w in words),
              "untranscribed_voiced": sum(u.kind == "voiced" for u in unt),
              "untranscribed_unvoiced": sum(u.kind == "unvoiced" for u in unt)}
    return WordsFile(source_id=source_id, noise_floor_db=floor, speech_level_db=speech, threshold_db=thr,
                     words=words, untranscribed=unt, stats=stats), env


def load_words(path: Path) -> WordsFile:
    return WordsFile.model_validate_json(Path(path).read_text(encoding="utf-8"))
