"""Audio perception: energy envelope, noise floor, loudness, clipping, silences, voicing.

All measurements come from the 48 kHz FLAC extracted at proxy time (the same samples
the renderer will use), so what the agent reads is what the viewer will hear.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from cutroom.errors import MediaError
from cutroom.system.ffmpeg import run_ffmpeg

ENV_WIN = 0.020   # RMS window (s)
ENV_HOP = 0.005   # envelope resolution (s): word edges are placed to within 5 ms
DB_FLOOR = -100.0


def read_mono(path: Path, start: float | None = None, end: float | None = None) -> tuple[np.ndarray, int]:
    """Read (part of) an audio file as mono float32."""
    try:
        info = sf.info(str(path))
        sr = info.samplerate
        a = int(round((start or 0) * sr))
        b = int(round(end * sr)) if end is not None else -1
        x, sr = sf.read(str(path), start=a, stop=None if b < 0 else b, dtype="float32", always_2d=True)
    except (RuntimeError, sf.LibsndfileError) as e:  # type: ignore[attr-defined]
        raise MediaError(f"Cannot read audio {path}: {e}", fix="Re-extract with `cutroom proxy <job> --force`.") from e
    return x.mean(axis=1), sr


def to_db(x: np.ndarray) -> np.ndarray:
    return 20 * np.log10(np.maximum(x, 10 ** (DB_FLOOR / 20)))


def envelope_db(x: np.ndarray, sr: int, win: float = ENV_WIN, hop: float = ENV_HOP) -> np.ndarray:
    """RMS level in dBFS per hop. Frame i covers [i*hop, i*hop + win)."""
    w, h = max(1, int(win * sr)), max(1, int(hop * sr))
    if len(x) < w:
        return np.array([float(to_db(np.sqrt(np.mean(x**2) + 1e-20)))])
    c = np.concatenate([[0.0], np.cumsum(x.astype(np.float64) ** 2)])
    starts = np.arange(0, len(x) - w + 1, h)
    ms = (c[starts + w] - c[starts]) / w
    return to_db(np.sqrt(ms)).astype(np.float32)


def frame_time(i: int | np.ndarray, hop: float = ENV_HOP, win: float = ENV_WIN):
    """Centre time of envelope frame ``i``."""
    return i * hop + win / 2


def levels(env: np.ndarray) -> tuple[float, float, float]:
    """(noise floor, speech level, speech/silence threshold) in dBFS.

    Noise floor: 5th percentile of the envelope. Speech level: 90th percentile.
    Threshold: a third of the way from floor to speech, but at least 10 dB above the floor,
    so quiet consonants count as speech while room tone and breaths usually do not.
    """
    floor = float(np.percentile(env, 5))
    speech = float(np.percentile(env, 90))
    thr = max(floor + 10.0, floor + (speech - floor) / 3.0)
    return round(floor, 1), round(speech, 1), round(min(thr, speech - 3.0), 1)


def runs_above(env: np.ndarray, thr: float, min_frames: int = 1) -> list[tuple[int, int]]:
    """[start, end) frame index runs where env >= thr, at least ``min_frames`` long."""
    above = np.concatenate([[False], env >= thr, [False]])
    d = np.diff(above.astype(np.int8))
    starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    return [(int(s), int(e)) for s, e in zip(starts, ends) if e - s >= min_frames]


def silences(env: np.ndarray, thr: float, min_dur: float = 0.3) -> list[tuple[float, float]]:
    """Regions below ``thr`` lasting at least ``min_dur`` seconds."""
    below = runs_above(-env, -thr)  # env <= thr
    out = []
    for s, e in below:
        t0 = s * ENV_HOP
        t1 = (e - 1) * ENV_HOP + ENV_WIN
        if t1 - t0 >= min_dur:
            out.append((round(t0, 3), round(t1, 3)))
    return out


def periodicity(x: np.ndarray, sr: int, fmin: float = 70.0, fmax: float = 400.0,
                win: float = 0.04, hop: float = 0.01) -> np.ndarray:
    """Per-frame voicing strength 0..1 (peak normalised autocorrelation in the pitch range).

    Voiced sounds ("uh", "um", vowels) score high (> ~0.5); breaths, hiss and clicks score low.
    """
    w, h = int(win * sr), int(hop * sr)
    if len(x) < w:
        return np.zeros(0, np.float32)
    n = 1 + (len(x) - w) // h
    out = np.zeros(n, np.float32)
    nfft = 1 << int(np.ceil(np.log2(2 * w)))
    lo, hi = int(sr / fmax), min(int(sr / fmin), w - 1)
    hann = np.hanning(w).astype(np.float32)
    for c0 in range(0, n, 2000):  # chunked to bound memory
        idx = np.arange(c0, min(n, c0 + 2000))[:, None] * h + np.arange(w)[None, :]
        fr = x[idx]
        fr = (fr - fr.mean(axis=1, keepdims=True)) * hann
        spec = np.fft.rfft(fr, nfft, axis=1)
        ac = np.fft.irfft(np.abs(spec) ** 2, nfft, axis=1)[:, : w]
        r0 = ac[:, 0:1] + 1e-12
        out[c0: c0 + len(fr)] = np.clip(ac[:, lo:hi].max(axis=1) / r0[:, 0], 0, 1)
    return out


@dataclass
class Loudness:
    integrated_lufs: float | None
    lra_lu: float | None
    true_peak_dbtp: float | None


def measure_loudness(path: Path, start: float | None = None, end: float | None = None) -> Loudness:
    """EBU R128 integrated loudness, loudness range and true peak via FFmpeg's ebur128."""
    args = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if end is not None:
        args += ["-to", f"{end:.3f}"]
    args += ["-i", str(path), "-vn", "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"]
    log = run_ffmpeg(args, desc="loudness measurement")
    summary = log[log.rfind("Summary:"):] if "Summary:" in log else log

    def grab(pattern: str) -> float | None:
        m = re.search(pattern, summary)
        if not m:
            return None
        v = m.group(1)
        return None if v in ("-inf", "inf", "nan") else float(v)

    return Loudness(integrated_lufs=grab(r"I:\s+(-?[\d.]+|-inf)\s+LUFS"),
                    lra_lu=grab(r"LRA:\s+(-?[\d.]+)\s+LU"),
                    true_peak_dbtp=grab(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS"))


def clipping(x: np.ndarray, sr: int, level: float = 0.999, min_run: int = 3) -> dict:
    """Runs of >= ``min_run`` consecutive samples at full scale (true clipping, not single peaks)."""
    hot = np.abs(x) >= level
    if not hot.any():
        return {"clipped_runs": 0, "clipped_seconds": 0.0, "first_at": None}
    d = np.diff(np.concatenate([[0], hot.astype(np.int8), [0]]))
    s, e = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    keep = (e - s) >= min_run
    s, e = s[keep], e[keep]
    return {"clipped_runs": int(len(s)), "clipped_seconds": round(float((e - s).sum() / sr), 3),
            "first_at": round(float(s[0] / sr), 3) if len(s) else None}
