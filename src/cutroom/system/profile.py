"""The per-machine profile: every hardware-dependent decision in one file.

`cutroom doctor` detects the machine, benchmarks what matters and writes
``config/machine.json``. Every other command reads it, so model sizes, thread
counts, encoders and concurrency always match the machine they run on.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from cutroom import __version__
from cutroom.joblog import now_iso
from cutroom.paths import machine_profile_path


class ASRSettings(BaseModel):
    engine: Literal["faster-whisper"] = "faster-whisper"
    model: str = "small.en"
    device: Literal["cpu", "cuda"] = "cpu"
    compute_type: str = "int8"
    cpu_threads: int = 4
    beam_size: int = 5
    vad_filter: bool = True
    verbatim_prompt: bool = True
    condition_on_previous_text: bool = True
    selection: dict = Field(default_factory=dict, description="How and why this model was chosen.")


class RenderSettings(BaseModel):
    proxy_short_side: int = 540
    proxy_encoder: str = "libx264"
    preview_encoder: str = "libx264"
    final_encoder: str = Field("libx264", description="Software x264 for finals: best quality per bit.")
    concurrency: int = 2
    hw_encoders: list[str] = Field(default_factory=list)


class MachineProfile(BaseModel):
    schema_version: int = 1
    created_at: str = Field(default_factory=now_iso)
    cutroom_version: str = __version__
    machine: dict = Field(default_factory=dict)
    ffmpeg: dict = Field(default_factory=dict)
    cuda_devices: int = 0
    memory_budget_gb: float = 8.0
    asr: ASRSettings = Field(default_factory=ASRSettings)
    render: RenderSettings = Field(default_factory=RenderSettings)
    notes: list[str] = Field(default_factory=list)


# CPU candidates for `doctor --select-asr`. medium.en is deliberately absent: on our
# benchmark (4-core x86, 5:50 of real speech) large-v3-turbo was both faster
# (RTF 0.26 vs 0.47) and more accurate, so medium.en is never the best choice.
CPU_ASR_CANDIDATES = ["small.en", "large-v3-turbo"]


def default_asr(machine, cuda_devices: int) -> ASRSettings:
    """Heuristic choice before (or without) a benchmark."""
    threads = max(1, machine.cpu.physical_cores or 4)
    if cuda_devices > 0:
        vram = max((g.vram_gb or 0) for g in machine.gpus if g.vendor == "nvidia") if machine.has_nvidia else 0
        if vram >= 8:
            return ASRSettings(model="large-v3", device="cuda", compute_type="float16", cpu_threads=threads,
                               selection={"method": "heuristic", "reason": f"NVIDIA GPU with {vram} GB VRAM"})
        if vram >= 4:
            return ASRSettings(model="large-v3-turbo", device="cuda", compute_type="int8_float16",
                               cpu_threads=threads,
                               selection={"method": "heuristic", "reason": f"NVIDIA GPU with {vram} GB VRAM"})
    # CPU (including Apple Silicon, where CTranslate2 runs on the CPU cores).
    model = "large-v3-turbo" if machine.ram_total_gb >= 8 and threads >= 4 else "small.en"
    return ASRSettings(model=model, device="cpu", compute_type="int8", cpu_threads=threads,
                       selection={"method": "heuristic",
                                  "reason": f"CPU with {threads} physical cores, {machine.ram_total_gb} GB RAM; "
                                            "run `cutroom doctor --select-asr` to benchmark"})


def derive_profile(machine, ffinfo: dict | None = None, hw_encoders: list[str] | None = None,
                   cuda_devices: int = 0) -> MachineProfile:
    ram = machine.ram_total_gb
    budget = round(max(2.0, min(ram * 0.6, ram - 4.0)), 1)   # leave room for the OS and other apps
    cores = machine.cpu.physical_cores or 2
    concurrency = max(1, min(cores // 2, int(budget // 3)))
    hw = hw_encoders or []
    notes = []
    if hw:
        notes.append(f"Hardware encoder {hw[0]} used for previews; finals use libx264 for quality.")
    else:
        notes.append("No working hardware video encoder; all encoding uses libx264 (software).")
    return MachineProfile(
        machine=machine.to_dict(), ffmpeg=ffinfo or {}, cuda_devices=cuda_devices,
        memory_budget_gb=budget, asr=default_asr(machine, cuda_devices),
        render=RenderSettings(preview_encoder=hw[0] if hw else "libx264", concurrency=concurrency,
                              hw_encoders=hw),
        notes=notes)


def load_profile() -> MachineProfile:
    """The saved profile, or a quick heuristic one if `cutroom doctor` has not run yet."""
    path = machine_profile_path()
    if path.is_file():
        try:
            return MachineProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 - fall back to a fresh heuristic profile
            pass
    from cutroom.system.detect import detect_machine

    return derive_profile(detect_machine())


def save_profile(profile: MachineProfile) -> None:
    from cutroom.job import atomic_write_text

    atomic_write_text(machine_profile_path(), profile.model_dump_json(indent=2) + "\n")
