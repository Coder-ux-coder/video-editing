"""Local speech-to-text with word timestamps (faster-whisper / CTranslate2).

Audio is decoded once by FFmpeg into a 16 kHz mono WAV (see ingest.proxy), and
that exact file is what the model hears, so word times map 1:1 to the source.
"""

from __future__ import annotations

import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np
import psutil
from pydantic import BaseModel, Field

from cutroom.errors import CutroomError, MediaError
from cutroom.joblog import now_iso
from cutroom.perception.models import ensure_model, get_spec
from cutroom.system.profile import ASRSettings

SAMPLE_RATE = 16000

# Whisper is trained to write clean prose and tends to drop "um"/"uh". A prompt
# written in a disfluent style nudges it toward verbatim output, which the
# filler-removal step needs. (Widely used technique; verified per model in Phase 1.)
VERBATIM_PROMPT = "Umm, let me think like, hmm... Okay, here's what I'm, like, thinking."

# Rough peak RAM for CPU int8 inference, in GB, used to refuse loads that would OOM.
_RAM_NEED_GB = {"tiny.en": 0.6, "base.en": 0.8, "small.en": 1.3, "medium.en": 2.6,
                "distil-large-v3": 2.8, "large-v3-turbo": 3.0, "large-v3": 5.0}

LOW_CONFIDENCE = 0.5


class Word(BaseModel):
    text: str
    start: float
    end: float
    prob: float


class Segment(BaseModel):
    id: int
    start: float
    end: float
    text: str
    avg_logprob: float
    no_speech_prob: float
    compression_ratio: float
    words: list[Word] = Field(default_factory=list)


class Transcript(BaseModel):
    schema_: str = Field("cutroom.transcript/1", alias="schema")
    source_id: str | None = None
    audio: str
    time_offset: float = Field(0.0, description="Added to every time so they are in source time.")
    duration: float
    language: str = "en"
    engine: str
    model: str
    model_revision: str
    device: str
    compute_type: str
    options: dict
    created_at: str = Field(default_factory=now_iso)
    elapsed_s: float
    rtf: float = Field(description="Processing time / audio duration (lower is faster).")
    segments: list[Segment]

    model_config = {"populate_by_name": True}

    @property
    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    def stats(self) -> dict:
        words = self.words
        low = [w for w in words if w.prob < LOW_CONFIDENCE]
        return {"segments": len(self.segments), "words": len(words), "low_confidence_words": len(low),
                "mean_word_prob": round(float(np.mean([w.prob for w in words])), 3) if words else None,
                "rtf": self.rtf, "elapsed_s": self.elapsed_s}


def load_wav_16k(path: Path) -> np.ndarray:
    try:
        with wave.open(str(path), "rb") as w:
            if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise MediaError(f"{path} is not 16 kHz mono 16-bit PCM.",
                                 fix="Re-extract audio with `cutroom proxy <job> --force`.")
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as e:
        raise MediaError(f"Cannot read WAV {path}: {e}", fix="Re-extract audio with `cutroom proxy <job> --force`.") from e
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


_MODEL_CACHE: dict[tuple, object] = {}


def _load_model(asr: ASRSettings, *, allow_download: bool = True):
    from faster_whisper import WhisperModel

    key = (asr.model, asr.device, asr.compute_type, asr.cpu_threads)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    need = _RAM_NEED_GB.get(asr.model, 3.0)
    avail = psutil.virtual_memory().available / 1024**3
    if asr.device == "cpu" and avail < need + 1.0:
        raise CutroomError(
            f"Not enough free memory to load ASR model '{asr.model}' (needs ~{need:.1f} GB, {avail:.1f} GB free).",
            fix="Close other applications, or use a smaller model: `cutroom transcribe <job> --model small.en`.")
    path = ensure_model(asr.model, allow_download=allow_download)
    if asr.device == "cuda":
        from cutroom.system.cuda import preload_cuda_libraries

        preload_cuda_libraries()
    try:
        model = WhisperModel(str(path), device=asr.device, compute_type=asr.compute_type,
                             cpu_threads=asr.cpu_threads, num_workers=1)
    except Exception as e:  # noqa: BLE001
        if asr.device != "cpu":
            raise CutroomError(f"Could not load '{asr.model}' on {asr.device}: {e}",
                               fix="Run `cutroom doctor`; it falls back to CPU if the GPU libraries are missing.") from e
        raise
    _MODEL_CACHE[key] = model
    return model


def transcribe_audio(audio_path: Path, asr: ASRSettings, *, hotwords: list[str] | None = None,
                     time_offset: float = 0.0, source_id: str | None = None,
                     on_progress: Callable[[float], None] | None = None,
                     allow_download: bool = True) -> Transcript:
    import faster_whisper

    audio = load_wav_16k(Path(audio_path))
    duration = len(audio) / SAMPLE_RATE
    if duration < 0.1:
        raise MediaError(f"{audio_path} contains no audio.", fix="Check the source has an audible audio track.")
    model = _load_model(asr, allow_download=allow_download)
    options = {
        "language": "en", "task": "transcribe", "beam_size": asr.beam_size, "word_timestamps": True,
        "vad_filter": asr.vad_filter, "condition_on_previous_text": asr.condition_on_previous_text,
        "initial_prompt": VERBATIM_PROMPT if asr.verbatim_prompt else None,
        "hotwords": " ".join(hotwords) if hotwords else None,
    }
    t0 = time.perf_counter()
    seg_iter, _info = model.transcribe(audio, **options)
    segments: list[Segment] = []
    for s in seg_iter:
        words = [Word(text=w.word, start=round(w.start + time_offset, 3), end=round(w.end + time_offset, 3),
                      prob=round(float(w.probability), 4)) for w in (s.words or [])]
        segments.append(Segment(id=len(segments), start=round(s.start + time_offset, 3),
                                end=round(s.end + time_offset, 3), text=s.text.strip(),
                                avg_logprob=round(float(s.avg_logprob), 4),
                                no_speech_prob=round(float(s.no_speech_prob), 4),
                                compression_ratio=round(float(s.compression_ratio), 3), words=words))
        if on_progress:
            on_progress(min(1.0, s.end / duration))
    elapsed = time.perf_counter() - t0
    if on_progress:
        on_progress(1.0)
    spec = get_spec(asr.model)
    return Transcript(source_id=source_id, audio=str(audio_path), time_offset=time_offset,
                      duration=round(duration, 3), engine=f"faster-whisper {faster_whisper.__version__}",
                      model=asr.model, model_revision=spec.revision, device=asr.device,
                      compute_type=asr.compute_type, options=options, elapsed_s=round(elapsed, 2),
                      rtf=round(elapsed / duration, 3), segments=segments)


# --------------------------------------------------------------------------- readable outputs


def _ts(t: float, sep: str = ".") -> str:
    ms = int(round(t * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def readable_text(t: Transcript) -> str:
    """The agent's main reading view: timestamped lines, doubtful words marked ⟨?word?⟩."""
    st = t.stats()
    lines = [f"# Transcript {t.source_id or ''} | {t.model} ({t.compute_type}, {t.device}) | "
             f"{t.duration:.1f}s | {st['words']} words | {st['low_confidence_words']} low-confidence "
             f"(p<{LOW_CONFIDENCE}) marked ⟨?word?⟩", ""]
    for s in t.segments:
        if s.words:
            body = "".join(f" ⟨?{w.text.strip()}?⟩" if w.prob < LOW_CONFIDENCE else w.text for w in s.words).strip()
        else:
            body = s.text
        lines.append(f"[{_ts(s.start)} → {_ts(s.end)}] {body}")
    return "\n".join(lines) + "\n"


def to_srt(t: Transcript) -> str:
    """Segment-level SRT (proper caption phrasing comes with the caption engine in Phase 2)."""
    out = []
    for i, s in enumerate(t.segments, 1):
        out.append(f"{i}\n{_ts(s.start, ',')} --> {_ts(s.end, ',')}\n{s.text.strip()}\n")
    return "\n".join(out)


def save_transcript(t: Transcript, out_dir: Path) -> dict[str, Path]:
    from cutroom.job import atomic_write_text

    paths = {"json": out_dir / "transcript.json", "txt": out_dir / "transcript.txt",
             "srt": out_dir / "transcript.srt"}
    atomic_write_text(paths["json"], t.model_dump_json(indent=1, by_alias=True) + "\n")
    atomic_write_text(paths["txt"], readable_text(t))
    atomic_write_text(paths["srt"], to_srt(t))
    return paths


def load_transcript(path: Path) -> Transcript:
    return Transcript.model_validate_json(Path(path).read_text(encoding="utf-8"))
