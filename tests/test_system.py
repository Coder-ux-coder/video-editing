import pytest

from cutroom.system.detect import CPUInfo, GPUInfo, MachineInfo, OSInfo, detect_machine
from cutroom.system.profile import derive_profile

from conftest import needs_ffmpeg


def _machine(ram=16.0, cores=4, gpus=(), system="Linux", arch="x86_64"):
    return MachineInfo(os=OSInfo(system=system, pretty=system, release="", arch=arch),
                       cpu=CPUInfo(model="cpu", physical_cores=cores, logical_cores=cores * 2),
                       ram_total_gb=ram, ram_available_gb=ram - 2, disk_free_gb=100, disk_path="/",
                       gpus=list(gpus))


def test_detect_machine_reports_basics():
    m = detect_machine()
    assert m.os.system in ("Linux", "Darwin", "Windows")
    assert m.ram_total_gb > 0
    assert (m.cpu.logical_cores or 0) >= 1
    assert m.to_dict()["os"]["arch"]


def test_profile_cpu_16gb_4_cores_stays_safe():
    p = derive_profile(_machine(ram=16, cores=4))
    assert p.asr.device == "cpu" and p.asr.compute_type == "int8"
    assert p.memory_budget_gb <= 12
    assert p.render.concurrency == 2
    assert p.render.final_encoder == "libx264"


def test_profile_nvidia_gpu_uses_cuda():
    gpu = GPUInfo(vendor="nvidia", name="RTX 3060", vram_gb=12)
    p = derive_profile(_machine(gpus=[gpu]), cuda_devices=1)
    assert p.asr.device == "cuda" and p.asr.model == "large-v3"


def test_profile_small_gpu_and_no_cuda_falls_back():
    gpu = GPUInfo(vendor="nvidia", name="GTX 1050", vram_gb=2)
    assert derive_profile(_machine(gpus=[gpu]), cuda_devices=1).asr.device == "cpu"
    big = GPUInfo(vendor="nvidia", name="RTX 4090", vram_gb=24)
    assert derive_profile(_machine(gpus=[big]), cuda_devices=0).asr.device == "cpu"


def test_profile_prefers_hw_encoder_for_previews_only():
    p = derive_profile(_machine(), hw_encoders=["h264_nvenc"])
    assert p.render.preview_encoder == "h264_nvenc"
    assert p.render.final_encoder == "libx264"


def test_select_asr_model_picks_most_accurate_within_budget():
    from cutroom.doctor import select_asr_model

    results = [
        {"model": "large-v3-turbo", "rtf": 0.9, "wer": 0.02, "peak_rss_gb": 2.5},
        {"model": "medium.en", "rtf": 0.5, "wer": 0.03, "peak_rss_gb": 2.0},
        {"model": "small.en", "rtf": 0.2, "wer": 0.05, "peak_rss_gb": 1.0},
    ]
    assert select_asr_model(results, budget=0.6)[0] == "medium.en"
    assert select_asr_model(results, budget=1.0)[0] == "large-v3-turbo"
    assert select_asr_model(results, budget=0.1)[0] is None


@needs_ffmpeg
def test_ffmpeg_capabilities():
    from cutroom.system.ffmpeg import ffmpeg_info

    info = ffmpeg_info()
    assert info.major and info.major >= 6
    assert "loudnorm" in info.filters and "silencedetect" in info.filters
    assert "libx264" in info.encoders
    assert info.missing_required_filters == []


@needs_ffmpeg
def test_run_ffmpeg_error_has_fix(tmp_path):
    import pytest

    from cutroom.errors import CutroomError
    from cutroom.system.ffmpeg import run_ffmpeg

    with pytest.raises(CutroomError) as e:
        run_ffmpeg(["-i", str(tmp_path / "missing.mp4"), "-f", "null", "-"], desc="test")
    assert e.value.fix and "path" in e.value.fix.lower()


def test_cuda_preload_is_harmless_without_gpu():
    from cutroom.system.cuda import preload_cuda_libraries

    assert isinstance(preload_cuda_libraries(), list)


def test_select_asr_model_prefers_better_timing_then_quality():
    from cutroom.doctor import select_asr_model

    small = {"model": "small.en", "rtf": 0.2, "wer": 0.02, "peak_rss_gb": 0.8, "timing_err_max_s": 0.30}
    turbo = {"model": "large-v3-turbo", "rtf": 0.25, "wer": 0.02, "peak_rss_gb": 2.5, "timing_err_max_s": 0.45}
    assert select_asr_model([small, turbo])[0] == "small.en"          # clearly better timing wins
    turbo_close = turbo | {"timing_err_max_s": 0.34}
    assert select_asr_model([small, turbo_close])[0] == "large-v3-turbo"  # within 50 ms -> quality rank
    tidy = turbo_close | {"keeps_stumble": False}
    assert select_asr_model([small | {"keeps_stumble": True}, tidy])[0] == "small.en"  # faithfulness first
    turbo_bad = turbo | {"timing_err_max_s": 0.9}
    assert select_asr_model([turbo_bad])[0] is None                   # fails the timing gate


def test_boundary_errors_assign_words_to_best_sentence():
    from types import SimpleNamespace as NS

    from cutroom.doctor import boundary_errors

    sentences = [{"start": 0.0, "end": 1.0}, {"start": 1.5, "end": 2.5}]
    words = [NS(start=0.0, end=0.5), NS(start=0.5, end=1.0), NS(start=1.2, end=1.8), NS(start=1.8, end=2.5)]
    # The early-starting third word belongs to sentence 2, not sentence 1.
    assert boundary_errors(NS(words=words), sentences) == [0.0, 0.0, pytest.approx(0.3), 0.0]
