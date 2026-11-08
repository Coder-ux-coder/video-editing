"""Procedural music beds and sound effects (numpy only). Always available, no licence questions:
the zero-budget fallback when the library has nothing that fits.

Music beds are deliberately simple and unobtrusive (they sit 20 dB under speech): a slow chord
progression on soft detuned pads with a sub bass, an optional plucked arpeggio, and a long
synthetic reverb. One progression cycle is synthesised and reverberated circularly, so it loops
seamlessly to any length.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

SR = 48000
NOTE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}

# Moods: key, chords (root semitone offset from the key, minor?), seconds per chord, arpeggio.
MOODS = {
    "calm": {"key": "D", "chords": [(0, False), (7, False), (9, True), (5, False)], "bar_s": 4.0, "arp": False},
    "documentary": {"key": "A", "chords": [(0, True), (8, False), (3, False), (10, False)], "bar_s": 4.5, "arp": False},
    "upbeat": {"key": "C", "chords": [(0, False), (9, True), (5, False), (7, False)], "bar_s": 2.0, "arp": True},
    "warm": {"key": "F", "chords": [(0, False), (5, False), (9, True), (7, False)], "bar_s": 3.5, "arp": True},
}


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _soft_saw(f: float, t: np.ndarray, harmonics: int = 10, rng=None) -> np.ndarray:
    """Band-limited sawtooth with harmonics rolled off (a warm, filtered pad)."""
    out = np.zeros_like(t)
    for n in range(1, harmonics + 1):
        if f * n > 8000:
            break
        out += np.sin(2 * np.pi * f * n * t + (rng.uniform(0, 2 * np.pi) if rng is not None else 0)) / n ** 1.6
    return out


def _reverb_circular(x: np.ndarray, seconds: float = 2.4, seed: int = 7) -> np.ndarray:
    """Synthetic hall (decaying stereo noise impulse), applied by circular FFT convolution so a
    loop stays seamless."""
    rng = np.random.default_rng(seed)
    n = len(x)
    ir_len = min(n, int(seconds * SR))
    t = np.arange(ir_len) / SR
    out = np.empty_like(x)
    for ch in range(x.shape[1]):
        ir = rng.standard_normal(ir_len) * np.exp(-t * 6.9 / seconds)
        ir[: int(0.012 * SR)] = 0        # pre-delay
        ir /= np.sqrt((ir ** 2).sum())
        full = np.zeros(n)
        full[:ir_len] = ir
        out[:, ch] = np.real(np.fft.ifft(np.fft.fft(x[:, ch]) * np.fft.fft(full)))
    return out


def music_bed(mood: str = "calm", seconds: float = 30.0, seed: int = 1) -> np.ndarray:
    """Stereo float32 bed of ``seconds`` (loops the progression; fades in and out)."""
    if mood not in MOODS:
        raise KeyError(f"unknown mood {mood}; choose {', '.join(MOODS)}")
    m = MOODS[mood]
    rng = np.random.default_rng(seed)
    key = 50 + NOTE[m["key"]]            # D3 = 50
    bar = m["bar_s"]
    cycle = bar * len(m["chords"])
    n = int(cycle * SR)
    t = np.arange(n) / SR
    loop = np.zeros((n, 2))
    xf = 1.2                              # chord crossfade (s)

    def q(f: float) -> float:
        """A whole number of cycles per loop, so every oscillator meets itself at the loop point."""
        return round(f * cycle) / cycle
    for k, (root, minor) in enumerate(m["chords"]):
        tones = [0, 3 if minor else 4, 7, 14]          # root, third, fifth, ninth
        a, b = k * bar, (k + 1) * bar
        env = np.zeros_like(t)
        for shift in (-cycle, 0.0, cycle):     # circular: tails wrap around the loop point
            tt = t + shift
            e = np.clip(np.minimum((tt - a + xf) / xf, (b + xf - tt) / xf), 0, 1)
            env = np.maximum(env, np.where((tt > a - xf) & (tt < b + xf), e, 0.0))
        env = env ** 1.5
        for j, iv in enumerate(tones):
            f = _hz(key + root + iv + (12 if j == 3 else 0))
            for ch, det in ((0, -0.08), (1, 0.08)):
                pad = (_soft_saw(q(f * 2 ** (det / 12)), t, rng=rng)
                       + 0.7 * _soft_saw(q(f * 2 ** (-det / 12)), t, rng=rng))
                loop[:, ch] += 0.06 * env * pad
        bass = np.sin(2 * np.pi * q(_hz(key + root - 12)) * t)
        loop += (0.10 * env * bass)[:, None]
        if m["arp"]:
            step = bar / 8
            for s in range(8):
                t0 = a + s * step
                f = q(_hz(key + root + 12 + [0, 7, 12, 4 if not minor else 3][s % 4]))
                tt = (t - t0) % cycle                     # circular: the last notes ring into the loop start
                pl = np.sin(2 * np.pi * f * tt) * np.exp(-tt * 7.0) * np.clip(tt / 0.004, 0, 1)
                pan = 0.35 + 0.3 * (s % 2)
                loop[:, 0] += 0.035 * pl * (1 - pan)
                loop[:, 1] += 0.035 * pl * pan
    wet = _reverb_circular(loop)
    loop = 0.65 * loop + 0.55 * wet
    reps = int(np.ceil(seconds / cycle))
    y = np.tile(loop, (reps, 1))[: int(seconds * SR)]
    fade = min(int(2.0 * SR), len(y) // 4)
    y[:fade] *= np.linspace(0, 1, fade)[:, None] ** 2
    y[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 2
    y /= max(1e-9, np.abs(y).max()) / 0.5
    return y.astype(np.float32)


# --------------------------------------------------------------------------- sound effects


def _bandpass_sweep(noise: np.ndarray, f0: float, f1: float, q: float = 1.5, block: int = 1024) -> np.ndarray:
    """Noise through a band-pass whose centre sweeps f0 → f1 (block-wise FFT filtering)."""
    n = len(noise)
    out = np.zeros(n)
    win = np.hanning(block * 2)
    freqs = np.fft.rfftfreq(block * 2, 1 / SR)
    for start in range(0, n - block, block):
        seg = noise[start:start + block * 2]
        if len(seg) < block * 2:
            break
        c = f0 * (f1 / f0) ** (start / max(1, n - block))
        bw = c / q
        resp = np.exp(-0.5 * ((freqs - c) / bw) ** 2)
        out[start:start + block * 2] += np.fft.irfft(np.fft.rfft(seg * win) * resp) * 0.5
    return out


def sfx(kind: str, seed: int = 3) -> np.ndarray:
    """Stereo float32 sound effect: whoosh, pop, impact, riser, ding."""
    rng = np.random.default_rng(seed)
    if kind == "whoosh":
        d = 0.65
        n = int(d * SR)
        t = np.arange(n) / SR
        x = _bandpass_sweep(rng.standard_normal(n + 2048), 400, 3200)[:n]
        env = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 2
        y = np.stack([x * env * (1 - t / d * 0.6), x * env * (0.4 + t / d * 0.6)], 1)
    elif kind == "riser":
        d = 1.6
        n = int(d * SR)
        t = np.arange(n) / SR
        x = _bandpass_sweep(rng.standard_normal(n + 2048), 200, 6000, q=2.5)[:n]
        tone = np.sin(2 * np.pi * (200 * t + 400 * t ** 2 / d))
        env = (t / d) ** 2
        y = np.stack([(x + 0.3 * tone) * env] * 2, 1)
    elif kind == "impact":
        d = 0.9
        n = int(d * SR)
        t = np.arange(n) / SR
        f = 70 * np.exp(-t * 3) + 38
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 5)
        click = rng.standard_normal(n) * np.exp(-t * 60) * 0.4
        y = np.stack([body + click] * 2, 1)
    elif kind == "pop":
        d = 0.12
        n = int(d * SR)
        t = np.arange(n) / SR
        f = 900 * np.exp(-t * 25) + 300
        y = np.stack([np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 40)] * 2, 1)
    elif kind == "ding":
        d = 1.2
        n = int(d * SR)
        t = np.arange(n) / SR
        x = (np.sin(2 * np.pi * 1318.5 * t) + 0.4 * np.sin(2 * np.pi * 2637 * t)) * np.exp(-t * 4)
        y = np.stack([x] * 2, 1)
    else:
        raise KeyError(f"unknown sound effect {kind}; choose whoosh, riser, impact, pop, ding")
    fade = int(0.004 * SR)
    y[:fade] *= np.linspace(0, 1, fade)[:, None]
    y[-fade:] *= np.linspace(1, 0, fade)[:, None]
    y /= max(1e-9, np.abs(y).max()) / 0.7
    return y.astype(np.float32)


SFX_KINDS = ("whoosh", "riser", "impact", "pop", "ding")


def write(path: Path, y: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), y, SR, subtype="FLOAT")
    return path
