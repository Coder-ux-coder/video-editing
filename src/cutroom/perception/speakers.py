"""Who speaks when (speaker diarization), locally, with pyannote's free community-1 pipeline.

The model (CC-BY-4.0) is gated on Hugging Face: a free account that has accepted its conditions
and a read token are needed once, to download it (a person's decision; see CHARTER 4.11). It then
runs offline from ``models/speakers/``. pyannote.audio's usage telemetry (on by default, sending
file durations and speaker counts to pyannote's server) is switched off before the library loads:
nothing about client media leaves the machine.

Speakers are assigned per sentence (sentence numbering as in ``topics.task.md``), by majority of
time: the model's word-sized blips at sentence starts ("Well,", "And then,") do not split a
sentence, while a whole question from an off-mic interviewer does.

Output: ``analysis/<source>/speakers.json``.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path

from cutroom.errors import RefusedError, ToolMissingError
from cutroom.job import Job, SourceRecord, atomic_write_text
from cutroom.paths import models_dir

# Before any import of pyannote: its telemetry module reads this once to set its default (on).
os.environ["PYANNOTE_METRICS_ENABLED"] = "0"

MODEL = {"name": "speaker-diarization-community-1", "repo": "pyannote/speaker-diarization-community-1",
         "revision": "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee", "licence": "CC-BY-4.0",
         "files": ["config.yaml", "README.md", "embedding/*", "plda/*", "segmentation/*"]}
SCHEMA = "cutroom.speakers/1"


def model_path() -> Path:
    return models_dir() / "speakers" / MODEL["name"]


def is_installed() -> bool:
    try:
        import pyannote.audio  # noqa: F401
    except ImportError:
        return False
    return True


def ensure_model() -> Path:
    """The pinned pipeline in models/speakers/ (downloaded once with the person's token)."""
    target = model_path()
    if (target / "config.yaml").is_file() and (target / "embedding" / "pytorch_model.bin").is_file():
        return target
    from huggingface_hub import snapshot_download

    try:
        snapshot_download(repo_id=MODEL["repo"], revision=MODEL["revision"], local_dir=str(target),
                          allow_patterns=MODEL["files"])
    except Exception as e:  # noqa: BLE001 (gated, offline, revoked token: one clear message)
        raise ToolMissingError(
            f"Could not download the speaker model {MODEL['repo']}: {type(e).__name__}.",
            fix="It is free but gated: sign in at huggingface.co, accept the conditions on "
                f"huggingface.co/{MODEL['repo']}, create a read token, then `hf auth login` (or set HF_TOKEN).") from e
    return target


def _pipeline():
    os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
    try:
        from pyannote.audio import Pipeline
        from pyannote.audio.telemetry import set_telemetry_metrics
    except ImportError as e:
        raise ToolMissingError("Speaker detection needs pyannote.audio, which is not installed.",
                               fix="pip install -r requirements-speakers.txt (CPU-only PyTorch; free)") from e
    set_telemetry_metrics(False)
    return Pipeline.from_pretrained(str(ensure_model()))


def turns_for(audio_path: Path, *, num_speakers: int | None = None, min_speakers: int | None = None,
              max_speakers: int | None = None) -> list[tuple[float, float, str]]:
    """Exclusive speaker turns (no overlap) for a mono 16 kHz WAV."""
    import soundfile as sf
    import torch

    audio, sr = sf.read(str(audio_path), dtype="float32", always_2d=True)
    pipe = _pipeline()
    kw = {k: v for k, v in (("num_speakers", num_speakers), ("min_speakers", min_speakers),
                            ("max_speakers", max_speakers)) if v}
    out = pipe({"waveform": torch.from_numpy(audio.mean(axis=1)[None, :].copy()), "sample_rate": sr}, **kw)
    return [(round(seg.start, 3), round(seg.end, 3), spk)
            for seg, _, spk in out.exclusive_speaker_diarization.itertracks(yield_label=True)]


def assign_sentences(sentences: list[dict], turns: list[tuple[float, float, str]]) -> list[dict]:
    """Each sentence's speaker by majority of overlapping time, with the share as confidence."""
    out = []
    for s in sentences:
        acc: dict[str, float] = defaultdict(float)
        for a, b, spk in turns:
            o = min(b, s["end"]) - max(a, s["start"])
            if o > 0:
                acc[spk] += o
        if acc:
            spk = max(acc, key=acc.get)
            conf = acc[spk] / max(sum(acc.values()), 1e-6)
        else:                                            # no speech found in it: nearest turn
            mid = (s["start"] + s["end"]) / 2
            spk = min(turns, key=lambda t: min(abs(t[0] - mid), abs(t[1] - mid)))[2] if turns else None
            conf = 0.0
        out.append({"n": s["n"], "start": round(s["start"], 3), "end": round(s["end"], 3), "speaker": spk,
                    "confidence": round(conf, 2), "text": s["text"]})
    return out


def summarize(sentences: list[dict]) -> list[dict]:
    secs: dict[str, float] = defaultdict(float)
    count: dict[str, int] = defaultdict(int)
    for s in sentences:
        if s["speaker"]:
            secs[s["speaker"]] += s["end"] - s["start"]
            count[s["speaker"]] += 1
    total = sum(secs.values()) or 1.0
    return sorted(({"id": k, "seconds": round(v, 1), "share": round(v / total, 3), "sentences": count[k]}
                   for k, v in secs.items()), key=lambda d: -d["seconds"])


def speakers_path(job: Job, sid: str) -> Path:
    return job.root / "analysis" / sid / "speakers.json"


def load(job: Job, sid: str) -> dict | None:
    p = speakers_path(job, sid)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def diarize(job: Job, s: SourceRecord, *, num_speakers: int | None = None, min_speakers: int | None = None,
            max_speakers: int | None = None, force: bool = False) -> dict:
    from cutroom.perception.pipeline import ensure_audio, ensure_words
    from cutroom.perception.topics import sentences as split

    have = load(job, s.id)
    asked = {"num_speakers": num_speakers, "min_speakers": min_speakers, "max_speakers": max_speakers}
    if have and not force and have.get("asked") == asked and have.get("model", {}).get("revision") == MODEL["revision"]:
        return have
    if not s.media.primary_audio:
        raise RefusedError(f"{s.id} has no audio.", fix="Speaker detection needs speech.")
    ensure_audio(job, s)
    wf = ensure_words(job, s)
    t0 = time.perf_counter()
    turns = turns_for(job.abspath(s.audio["asr"]), **asked)
    sents = assign_sentences(split(wf), turns)
    names = (have or {}).get("names", {})
    doc = {"schema": SCHEMA, "source": s.id, "model": {k: MODEL[k] for k in ("repo", "revision", "licence")},
           "asked": asked, "elapsed_s": round(time.perf_counter() - t0, 1), "names": names,
           "speakers": summarize(sents), "turns": [list(t) for t in turns], "sentences": sents}
    doc["main"] = doc["speakers"][0]["id"] if doc["speakers"] else None
    atomic_write_text(speakers_path(job, s.id), json.dumps(doc, indent=1) + "\n")
    others = [sp for sp in doc["speakers"] if sp["id"] != doc["main"]]
    job.log.note(f"{s.id}: {len(doc['speakers'])} speaker(s) found in {doc['elapsed_s']}s"
                 + (f"; besides the main voice: " + ", ".join(f"{o['id']} ({o['sentences']} sentence(s), {o['seconds']}s)"
                                                            for o in others) if others else ""))
    return doc


def set_names(job: Job, sid: str, names: dict[str, str]) -> dict:
    doc = load(job, sid)
    if doc is None:
        raise RefusedError(f"No speakers found for {sid} yet.", fix=f"Run `cutroom speakers {job.id}` first.")
    unknown = [k for k in names if k not in {sp["id"] for sp in doc["speakers"]}]
    if unknown:
        raise RefusedError(f"Unknown speaker id(s): {unknown}.", fix="Use the ids `cutroom speakers` lists.")
    doc["names"] = {**doc.get("names", {}), **names}
    atomic_write_text(speakers_path(job, sid), json.dumps(doc, indent=1) + "\n")
    job.log.decision(f"{sid} speakers named: " + ", ".join(f"{k} = {v}" for k, v in names.items()),
                     "named by the agent from what they say")
    return doc


def label(doc: dict, spk: str | None) -> str:
    return doc.get("names", {}).get(spk, spk) if spk else "?"


def sentence_speakers(job: Job, sid: str) -> tuple[dict[int, str], str | None]:
    """{sentence number: speaker id} and the main speaker, or ({}, None) without diarization."""
    doc = load(job, sid)
    if not doc:
        return {}, None
    return {s["n"]: s["speaker"] for s in doc["sentences"]}, doc.get("main")


def speaker_maps(job: Job, tl) -> dict:
    """{source id: ({sentence number: speaker}, main speaker)} for the sources that have speakers.json."""
    out = {}
    for s in tl.sources:
        m, main = sentence_speakers(job, s.id)
        if m:
            out[s.id] = (m, main)
    return out
