"""Voice chain planned from measurements, applied with FFmpeg, verified by measurement.

Order (EDITING_STANDARDS.md §5): clean-up → EQ → compression → de-ess → loudness → limiter.
Every stage is subtle and only used when the measurements call for it: over-processing
(warbly denoise, pumping compression) is worse than the problem it fixes.
"""

from __future__ import annotations

from pathlib import Path

from cutroom.errors import CutroomError
from cutroom.perception.audio import measure_loudness
from cutroom.system.ffmpeg import run_ffmpeg


def plan_chain(report: dict, style: dict) -> dict:
    """Decide each stage from an audio report (analysis/<src>/audio_report.json)."""
    snr = report.get("snr_db") or 40.0
    floor = report.get("noise_floor_dbfs") or -60.0
    speech = report.get("speech_level_dbfs") or -25.0
    chain: dict = {"stages": [], "reasons": []}
    chain["highpass"] = 80
    chain["reasons"].append("high-pass 80 Hz: removes rumble and handling noise below the voice")
    if snr < 35:
        nr = 6 if snr >= 28 else (9 if snr >= 20 else 12)
        chain["denoise"] = {"nf": max(-80, min(-20, round(floor + 3))), "nr": nr}
        chain["reasons"].append(f"denoise {nr} dB (speech/noise {snr} dB < 35 dB); noise floor {floor} dBFS")
    else:
        chain["reasons"].append(f"no denoise: speech/noise {snr} dB is already clean (denoise would only add artefacts)")
    chain["eq"] = [{"f": 300, "g": -1.5, "q": 1.0}, {"f": 4000, "g": 1.5, "q": 0.8}]
    chain["reasons"].append("gentle EQ: −1.5 dB at 300 Hz (boxiness), +1.5 dB at 4 kHz (presence)")
    thr = round(speech - 8, 1)
    chain["compressor"] = {"threshold_db": thr, "ratio": 3.0, "attack_ms": 12, "release_ms": 150, "knee_db": 6}
    chain["reasons"].append(f"compression 3:1 above {thr} dBFS (8 dB under the measured speech level): evens out level")
    chain["deesser"] = {"i": 0.35, "m": 0.5, "f": 0.5}
    chain["reasons"].append("light de-essing")
    a = style.get("audio", {})
    chain["target_lufs"] = a.get("target_lufs", -14.0)
    chain["true_peak_dbtp"] = a.get("true_peak_dbtp", -1.0)
    chain["reasons"].append(f"loudness normalised to {chain['target_lufs']} LUFS, true peak ≤ {chain['true_peak_dbtp']} dBTP "
                            "(linear gain to target, then an oversampled limiter on peaks only)")
    return chain


def _db_to_lin(db: float) -> float:
    return 10 ** (db / 20)


def chain_filters(chain: dict) -> list[str]:
    f = []
    if chain.get("highpass"):
        f.append(f"highpass=f={chain['highpass']}:poles=2")
    if chain.get("denoise"):
        d = chain["denoise"]
        f.append(f"afftdn=nf={d['nf']}:nr={d['nr']}:tn=1")
    for band in chain.get("eq", []):
        f.append(f"equalizer=f={band['f']}:t=q:w={band['q']}:g={band['g']}")
    if chain.get("compressor"):
        c = chain["compressor"]
        f.append(f"acompressor=threshold={_db_to_lin(c['threshold_db']):.6f}:ratio={c['ratio']}:"
                 f"attack={c['attack_ms']}:release={c['release_ms']}:knee={_db_to_lin(c['knee_db']):.4f}")
    if chain.get("deesser"):
        d = chain["deesser"]
        f.append(f"deesser=i={d['i']}:m={d['m']}:f={d['f']}")
    return f


# Internal ceiling leaves headroom for the AAC encoder, which raises inter-sample peaks
# (measured up to ~1 dB at 160 kbps).
TP_MARGIN = 1.0
OVERSAMPLE = 192000


def _limiter(ceiling_db: float) -> str:
    """True-peak-style limiting: limit at 4x the sample rate, so peaks between samples count."""
    return (f"aresample={OVERSAMPLE},alimiter=limit={_db_to_lin(ceiling_db):.5f}:attack=2:release=60:"
            f"level=disabled,aresample=48000")


def apply_chain(in_wav: Path, out_wav: Path, chain: dict, *, on_progress=None, duration: float | None = None) -> dict:
    """Process ``in_wav`` → ``out_wav`` (48 kHz float WAV) and return measured loudness.

    Mastering-style: (1) measure the chain's output, (2) apply the linear gain that reaches the
    target, then an oversampled limiter that only touches peaks above the ceiling, (3) measure
    again and correct the small loudness loss caused by limiting. Dynamics stay natural: no
    dynamic loudness normaliser riding the gain.
    """
    pre = chain_filters(chain)
    target = chain.get("target_lufs", -14.0)
    ceiling = chain.get("true_peak_dbtp", -1.0) - TP_MARGIN
    steps = 3

    def prog(k: int):
        return (lambda p: on_progress((k + p) / steps)) if on_progress else None

    stage = out_wav.with_name(out_wav.stem + ".pre.wav")
    run_ffmpeg(["-y", "-i", str(in_wav), "-af", ",".join(pre) if pre else "anull", "-c:a", "pcm_f32le", str(stage)],
               desc="voice chain", duration=duration, on_progress=prog(0))
    before = measure_loudness(stage)
    if before.integrated_lufs is None:
        raise CutroomError("The edit has no measurable speech loudness.", fix="Check the timeline is not silent.")
    gain = target - before.integrated_lufs
    after = None
    for attempt in range(2):
        run_ffmpeg(["-y", "-i", str(stage), "-af", f"volume={gain:.3f}dB,{_limiter(ceiling)}", "-c:a", "pcm_f32le",
                    str(out_wav)], desc="loudness + limiter", duration=duration, on_progress=prog(1 + attempt))
        after = measure_loudness(out_wav)
        miss = target - (after.integrated_lufs or target)
        if abs(miss) <= 0.3:
            break
        gain += miss          # limiting lowered the loudness a little: add it back, limiter holds peaks
    stage.unlink(missing_ok=True)
    return {"filters": pre + [f"volume={gain:.2f}dB", f"oversampled limiter at {ceiling} dBTP"],
            "mode": "linear gain + true-peak limiter", "gain_db": round(gain, 2),
            "before_lufs": before.integrated_lufs, "after_lufs": after.integrated_lufs,
            "after_true_peak_dbtp": after.true_peak_dbtp, "after_lra": after.lra_lu}
