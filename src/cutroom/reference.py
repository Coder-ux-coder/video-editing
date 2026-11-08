"""Learn a style from a reference video: measure how it is cut and paced, write a style file.

What is measured (and only that): camera/shot changes (FFmpeg scene detection), speech rate and
the pauses the editor left between words and between sentences (refined word timings), and
loudness. The style file inherits the defaults and overrides the pacing and pause parameters,
with every value's measurement written next to it. Taste that cannot be measured (graphics look,
music choice) is listed as a note for the agent, not guessed.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from cutroom.errors import RefusedError
from cutroom.job import Job, atomic_write_text
from cutroom.style import styles_dir


def _pct(a, q):
    return float(np.percentile(a, q)) if len(a) else None


def measure(job: Job) -> dict:
    s = job.manifest.sources[0]
    adir = job.root / "analysis" / s.id
    vis = json.loads((adir / "visual_report.json").read_text(encoding="utf-8"))
    aud = json.loads((adir / "audio_report.json").read_text(encoding="utf-8"))
    words = json.loads((adir / "words.json").read_text(encoding="utf-8"))["words"]
    shots = [sh["end"] - sh["start"] for sh in vis.get("shots", [])]
    dur = vis.get("duration_s") or s.media.duration
    mid, end = [], []
    for a, b in zip(words, words[1:]):
        gap = b["start"] - a["end"]
        if gap < 0.02:
            continue
        (end if a.get("sentence_end") else mid).append(gap)
    speech_time = sum(w["end"] - w["start"] for w in words)
    return {"duration_s": round(dur, 1), "shots": len(shots),
            "shot_s": {"median": _r(_pct(shots, 50)), "p25": _r(_pct(shots, 25)), "p75": _r(_pct(shots, 75)),
                       "p90": _r(_pct(shots, 90))},
            "cuts_per_minute": round(len(shots) / max(dur / 60, 1e-6), 2),
            "words_per_minute": round(len(words) / max(dur / 60, 1e-6), 1),
            "speech_coverage": round(speech_time / max(dur, 1e-6), 2),
            "pause_mid_s": {"median": _r(_pct(mid, 50)), "p90": _r(_pct(mid, 90))},
            "pause_sentence_s": {"median": _r(_pct(end, 50)), "p90": _r(_pct(end, 90))},
            "loudness_lufs": aud.get("integrated_lufs"),
            "footage_type": (vis.get("footage_type") or {}).get("type")}


def _r(x):
    return None if x is None else round(x, 3)


def _clamp(x, lo, hi, default):
    return default if x is None else round(min(hi, max(lo, x)), 2)


def derive_style(m: dict) -> dict:
    ed, pc = {}, {}
    ed["keep_pause_mid_sentence"] = _clamp(m["pause_mid_s"]["median"], 0.08, 0.35, 0.18)
    ed["keep_pause_sentence_end"] = _clamp(m["pause_sentence_s"]["median"], 0.15, 0.6, 0.32)
    ed["max_pause"] = _clamp(m["pause_sentence_s"]["p90"], max(0.3, ed["keep_pause_sentence_end"] + 0.1), 1.0, 0.5)
    lo, hi = m["shot_s"]["p25"], m["shot_s"]["p75"]
    if lo is not None and hi is not None and m["shots"] >= 4:
        pc["visual_change_target_s"] = [_clamp(lo, 1.0, 10.0, 3.0), _clamp(hi, 2.0, 15.0, 6.0)]
        pc["max_static_s"] = _clamp(m["shot_s"]["p90"], 4.0, 20.0, 12.0)
    return {"editing": ed, "pacing": pc}


def write_style(name: str, ref: str, m: dict, force: bool = False) -> Path:
    import yaml

    p = styles_dir() / f"{name}.style.yaml"
    if p.exists() and not force:
        raise RefusedError(f"Style '{name}' exists.", fix="Pick another name, or pass --force to replace it.")
    d = derive_style(m)
    head = [f"# Style learned from the reference video {ref} (cutroom analyze-reference).",
            "# Measured: " + json.dumps(m),
            "# Inherits everything else from default.style.yaml. Pauses are what the reference's",
            "# editor left between words (median) and before cutting (90th percentile); pacing comes from",
            "# its shot lengths. Not measurable here (agent to judge from the reference): graphics look,",
            "# caption style, music, colour.", f"name: {name}"]
    body = yaml.safe_dump(d, sort_keys=False)
    atomic_write_text(p, "\n".join(head) + "\n" + body)
    return p
