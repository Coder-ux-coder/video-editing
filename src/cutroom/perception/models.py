"""Registry of local ASR models, pinned to exact HuggingFace revisions.

All models are CTranslate2 conversions of OpenAI Whisper (MIT licence) and run
fully offline once downloaded. Pinning the revision keeps transcripts
reproducible: the same audio + model revision + options = the same words.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from cutroom.errors import ToolMissingError
from cutroom.paths import models_dir

# Files faster-whisper needs; we skip anything else the repos contain.
_ALLOW = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


@dataclass(frozen=True)
class ASRModelSpec:
    name: str
    repo: str
    revision: str
    download_mb: int
    english_only: bool
    quality_rank: int        # higher = more accurate (rough, from public benchmarks + our tests)
    licence: str = "MIT"
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


ASR_MODELS: dict[str, ASRModelSpec] = {
    m.name: m
    for m in [
        ASRModelSpec("tiny.en", "Systran/faster-whisper-tiny.en",
                     "0d3d19a32d3338f10357c0889762bd8d64bbdeba", 75, True, 1,
                     notes="Smoke tests only; too inaccurate for editing."),
        ASRModelSpec("base.en", "Systran/faster-whisper-base.en",
                     "3d3d5dee26484f91867d81cb899cfcf72b96be6c", 145, True, 2,
                     notes="Very low-end fallback."),
        ASRModelSpec("small.en", "Systran/faster-whisper-small.en",
                     "d1d751a5f8271d482d14ca55d9e2deeebbae577f", 485, True, 3,
                     notes="Fast CPU default; good on clean speech."),
        ASRModelSpec("medium.en", "Systran/faster-whisper-medium.en",
                     "a29b04bd15381511a9af671baec01072039215e3", 1530, True, 4,
                     notes="Better names/jargon; ~3x slower than small.en on CPU."),
        ASRModelSpec("large-v3-turbo", "deepdml/faster-whisper-large-v3-turbo-ct2",
                     "4df90f75321148c3a29a9e2351b7ddf8f5b115a8", 1620, False, 5,
                     notes="Near large-v3 accuracy with a 4-layer decoder; best quality/speed on GPU and fast CPUs."),
        ASRModelSpec("distil-large-v3", "Systran/faster-distil-whisper-large-v3",
                     "c3058b475261292e64a0412df1d2681c06260fab", 1510, True, 5,
                     notes="English-only distillation; fast, but word timings are less reliable."),
        ASRModelSpec("large-v3", "Systran/faster-whisper-large-v3",
                     "edaa852ec7e145841d8ffdb056a99866b5f0a478", 3090, False, 6,
                     notes="Most accurate; practical only with an NVIDIA GPU (>= 6 GB VRAM)."),
    ]
}


def get_spec(name: str) -> ASRModelSpec:
    try:
        return ASR_MODELS[name]
    except KeyError:
        raise ToolMissingError(f"Unknown ASR model '{name}'.",
                               fix=f"Choose one of: {', '.join(ASR_MODELS)}") from None


def model_path(name: str) -> Path:
    return models_dir() / "asr" / name


def is_downloaded(name: str) -> bool:
    p = model_path(name)
    return (p / "model.bin").is_file() and (p / "config.json").is_file()


def ensure_model(name: str, *, allow_download: bool = True) -> Path:
    """Return the local folder of ``name``, downloading the pinned revision if needed."""
    spec = get_spec(name)
    target = model_path(name)
    if is_downloaded(name):
        return target
    if not allow_download:
        raise ToolMissingError(
            f"ASR model '{name}' is not downloaded.",
            fix=f"Run `cutroom models pull {name}` (about {spec.download_mb} MB, free, MIT licence).")
    try:
        from huggingface_hub import snapshot_download
    except ImportError as e:  # pragma: no cover - dependency of faster-whisper
        raise ToolMissingError("huggingface_hub is not installed.", fix="Re-run setup.") from e
    target.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(repo_id=spec.repo, revision=spec.revision, local_dir=str(target),
                          allow_patterns=_ALLOW)
    except Exception as e:  # noqa: BLE001 - network errors come in many types
        raise ToolMissingError(
            f"Could not download ASR model '{name}' from huggingface.co: {e}",
            fix="Check your internet connection (or set HF_ENDPOINT to a mirror) and retry "
                f"`cutroom models pull {name}`.") from e
    if not is_downloaded(name):
        raise ToolMissingError(f"Download of '{name}' finished but model files are missing.",
                               fix=f"Delete {target} and retry `cutroom models pull {name}`.")
    return target
