"""Music and sound effects mixed under the processed voice, before loudness mastering.

Levels are set relative to the measured speech level, not absolute: music sits 12 dB under
speech in the open and dips a further 8 dB while anyone speaks (ducking with look-ahead), so it
never fights the voice; sound effects sit 8 dB under speech. The mix reports the measured
speech-to-music ratio during speech so the review can verify it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from cutroom.system.ffmpeg import require_tool

SR = 48000


def _decode(path: Path, scratch: Path) -> np.ndarray:
    """Any audio file → 48 kHz stereo float32 (FFmpeg decodes, so every format works)."""
    ff = require_tool("ffmpeg")
    raw = subprocess.run([str(ff), "-hide_banner", "-loglevel", "error", "-i", str(path), "-ac", "2", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def _rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + 1e-12))


def speech_mask(words_spans: list[tuple[float, float]], n: int, pad: float = 0.08) -> np.ndarray:
    m = np.zeros(n, bool)
    for a, b in words_spans:
        m[max(0, int((a - pad) * SR)):min(n, int((b + pad) * SR))] = True
    return m


def _duck_curve(mask: np.ndarray, depth_db: float, attack: float = 0.15, release: float = 0.5) -> np.ndarray:
    """Gain (linear) that dips ``depth_db`` while speech is on: reaches the dip ``attack`` s before
    speech starts (look-ahead) and recovers over ``release`` s after it ends."""
    hop = 480                                   # 10 ms control rate
    k = len(mask) // hop + 1
    ctl = np.array([mask[i * hop:(i + 1) * hop].any() for i in range(k)], float)
    a_n, r_n = max(1, int(attack * 100)), max(1, int(release * 100))
    look = np.convolve(ctl, np.ones(a_n), mode="full")[a_n - 1:a_n - 1 + k] > 0   # speech within the next attack window
    target = np.where(look | (ctl > 0), 1.0, 0.0)
    g = np.zeros(k)
    for i in range(1, k):
        rate = 1.0 / a_n if target[i] > g[i - 1] else 1.0 / r_n
        g[i] = g[i - 1] + np.clip(target[i] - g[i - 1], -rate, rate)
    lin = 10 ** (-depth_db * g / 20)
    return np.interp(np.arange(len(mask)), np.arange(k) * hop, lin)


def mix_beds(voice_wav: Path, out_wav: Path, beds: list[dict], speech_spans: list[tuple[float, float]], *,
             cfg: dict, scratch: Path) -> dict:
    """``beds``: [{kind, path, start, end, src_in, gain_db, fade_in, fade_out, duck}] in this
    render's time. Writes voice + beds to ``out_wav``; returns measurements."""
    voice, sr = sf.read(str(voice_wav), dtype="float32", always_2d=True)
    if voice.shape[1] == 1:
        voice = np.repeat(voice, 2, axis=1)
    n = len(voice)
    mask = speech_mask(speech_spans, n)
    v_db = _rms_db(voice[mask]) if mask.any() else _rms_db(voice)
    under = cfg.get("music_under_speech_db", 12.0)
    depth = cfg.get("duck_db", 8.0)
    sfx_under = cfg.get("sfx_under_speech_db", 8.0)
    music_bus = np.zeros_like(voice)
    sfx_bus = np.zeros_like(voice)
    cache: dict[str, np.ndarray] = {}
    for b in beds:
        a0, a1 = int(max(0.0, b["start"]) * SR), int(min(n / SR, b["end"]) * SR)
        if a1 <= a0:
            continue
        x = cache.setdefault(b["path"], _decode(Path(b["path"]), scratch))
        off = int(max(0.0, b.get("src_in", 0.0)) * SR)
        need = a1 - a0
        seg = x[off:off + need]
        if b["kind"] == "music" and len(seg) < need and len(x):
            seg = np.resize(x[off:] if off < len(x) else x, (need, 2))       # loop the bed
        seg = seg[:need]
        if not len(seg):
            continue
        if b["kind"] == "music":
            ref = _rms_db(x[off:off + min(len(x) - off, 30 * SR)]) if len(x) > off else _rms_db(seg)
            g = 10 ** ((v_db - under - ref + b.get("gain_db", 0.0)) / 20)
        else:
            g = 10 ** ((v_db - sfx_under - _rms_db(seg) + b.get("gain_db", 0.0)) / 20)
        seg = seg * g
        fi, fo = int(b.get("fade_in", 0.0) * SR), int(b.get("fade_out", 0.0) * SR)
        if fi:
            seg[:fi] *= np.linspace(0, 1, min(fi, len(seg)))[:, None] ** 2
        if fo:
            seg[-fo:] *= np.linspace(1, 0, min(fo, len(seg)))[:, None] ** 2
        (music_bus if b["kind"] == "music" else sfx_bus)[a0:a0 + len(seg)] += seg
    if any(b["kind"] == "music" and b.get("duck", True) for b in beds) and mask.any():
        music_bus *= _duck_curve(mask, depth)[:, None]
    mix = voice + music_bus + sfx_bus
    sf.write(str(out_wav), mix, SR, subtype="FLOAT")
    stats = {"beds": len(beds), "speech_db": round(v_db, 1)}
    on = mask & (np.abs(music_bus).max(axis=1) > 1e-6)
    if on.any():
        stats["speech_to_music_db"] = round(v_db - _rms_db(music_bus[on]), 1)
    off_speech = ~mask & (np.abs(music_bus).max(axis=1) > 1e-6)
    if off_speech.any():
        stats["music_in_pauses_db_under_speech"] = round(v_db - _rms_db(music_bus[off_speech]), 1)
    return stats
