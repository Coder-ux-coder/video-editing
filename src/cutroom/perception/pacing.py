"""Energy and pacing map: per-second speech rate, pauses, vocal energy and a "boring score"."""

from __future__ import annotations

import numpy as np

from cutroom.perception.audio import ENV_HOP
from cutroom.perception.words import WordsFile


def pacing_map(wf: WordsFile, env: np.ndarray, duration: float, window: float = 10.0) -> dict:
    """Per-second series plus the slowest/flattest stretches.

    boring score (0..1) per second = mean of: low speech rate, high pause share and low vocal
    energy, each scaled against this speaker's own range (so it ranks moments within a
    recording; it is not an absolute judgement of the content).
    """
    n = int(np.ceil(duration))
    words = np.zeros(n)
    speech = np.zeros(n)
    for w in wf.words:
        words[min(n - 1, int(w.start))] += 1
        a, b = w.start, w.end
        for sec in range(int(a), min(n, int(b) + 1)):
            speech[sec] += max(0.0, min(b, sec + 1) - max(a, sec))
    per = int(round(1 / ENV_HOP))
    energy = np.array([float(np.mean(env[i * per:(i + 1) * per])) if i * per < len(env) else -100.0 for i in range(n)])
    k = max(1, int(window))
    kern = np.ones(k) / k
    rate = np.convolve(words, kern, mode="same")             # words per second
    pause = 1 - np.clip(np.convolve(speech, kern, mode="same"), 0, 1)
    en = np.convolve(energy, kern, mode="same")

    def scaled(x: np.ndarray, invert: bool = False) -> np.ndarray:
        lo, hi = np.percentile(x, 5), np.percentile(x, 95)
        y = np.clip((x - lo) / max(hi - lo, 1e-6), 0, 1)
        return 1 - y if invert else y

    boring = (scaled(rate, True) + scaled(pause) + scaled(en, True)) / 3
    order = np.argsort(-boring)
    picked: list[int] = []
    for i in order:
        if all(abs(i - j) >= window for j in picked):
            picked.append(int(i))
        if len(picked) == 5:
            break
    wpm = 60 * len(wf.words) / max(duration, 1e-6)
    return {
        "words_per_minute": round(wpm, 1),
        "speech_seconds": round(float(speech.sum()), 1),
        "speech_coverage": round(float(speech.sum() / max(duration, 1e-6)), 3),
        "per_second": {"words": words.astype(int).tolist(), "speech_s": np.round(speech, 2).tolist(),
                       "energy_db": np.round(energy, 1).tolist(), "boring": np.round(boring, 3).tolist()},
        "slowest_stretches": [{"start": max(0, i - k // 2), "end": min(n, i + k // 2),
                               "boring": round(float(boring[i]), 2),
                               "text": wf.text_between(max(0, i - k // 2), min(n, i + k // 2))[:160]}
                              for i in sorted(picked)],
    }
