"""`cutroom doctor`: verify the whole toolchain on this machine, end to end.

It does not just check that things are installed: it ingests, proxies and
transcribes a known clip, and compares the transcript to the known script.
"""

from __future__ import annotations

import gc
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Callable, Literal

import psutil

from cutroom.errors import CutroomError
from cutroom.paths import samples_dir

Status = Literal["ok", "warn", "fail", "skip"]

MIN_RAM_GB = 8
RECOMMENDED_RAM_GB = 16
MIN_DISK_GB = 20
MIN_NODE_MAJOR = 18
SMOKE_MAX_WER = 0.15
SMOKE_MAX_BOUNDARY_ERR_S = 0.5
# Budget for the slowest acceptable CPU transcription (processing time / audio time).
ASR_RTF_BUDGET = 0.6


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    fix: str | None = None


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)
    profile: dict | None = None
    smoke: dict | None = None
    asr_benchmark: list[dict] | None = None

    def add(self, name: str, status: Status, detail: str, fix: str | None = None) -> Check:
        c = Check(name, status, detail, fix)
        self.checks.append(c)
        return c

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "checks": [asdict(c) for c in self.checks], "smoke": self.smoke,
                "asr_benchmark": self.asr_benchmark, "profile": self.profile}


def _pkg_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


# --------------------------------------------------------------------------- individual checks


def check_platform(r: DoctorReport, machine) -> None:
    o = machine.os
    if o.system not in ("Windows", "Darwin", "Linux"):
        r.add("platform", "fail", f"Unsupported OS {o.system}.", "Use Windows 10+, macOS 12+ or a recent Linux.")
        return
    extra = []
    if o.is_wsl:
        extra.append("WSL")
    if o.is_container:
        extra.append("container")
    r.add("platform", "ok", f"{o.pretty} ({o.arch}{', ' + ', '.join(extra) if extra else ''})")
    if o.system == "Windows" and o.windows_long_paths is False:
        r.add("windows long paths", "warn", "Long paths (>260 characters) are disabled.",
              "In an admin PowerShell: New-ItemProperty -Path "
              "'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem' -Name LongPathsEnabled "
              "-Value 1 -PropertyType DWORD -Force  (or keep jobs in a short folder via CUTROOM_JOBS_DIR)")


def check_python(r: DoctorReport, machine) -> None:
    v = sys.version_info
    if (v.major, v.minor) != (3, 12):
        r.add("python", "fail", f"Python {platform.python_version()} (need 3.12).",
              "Re-run setup; it creates a Python 3.12 virtual environment in .venv.")
    elif not machine.in_venv:
        r.add("python", "warn", f"Python {platform.python_version()} outside a virtual environment.",
              "Run Cutroom from .venv (setup creates it) so dependency versions stay pinned.")
    else:
        r.add("python", "ok", f"Python {platform.python_version()} in {sys.prefix}")


def check_packages(r: DoctorReport) -> None:
    pkgs = ["faster-whisper", "ctranslate2", "av", "numpy", "onnxruntime", "pydantic", "typer", "pyyaml", "psutil",
            "soundfile", "opencv-python-headless"]
    versions = {p: _pkg_version(p) for p in pkgs}
    missing = [p for p, v in versions.items() if v is None]
    if missing:
        r.add("python packages", "fail", f"Missing: {', '.join(missing)}", "Re-run setup (installs requirements.lock).")
        return
    av_major = int(versions["av"].split(".")[0])
    if av_major >= 17:
        r.add("python packages", "fail", f"av {versions['av']} is incompatible with faster-whisper 1.2.1.",
              "Re-run setup; requirements.lock pins av==16.1.0.")
        return
    r.add("python packages", "ok", ", ".join(f"{k} {v}" for k, v in versions.items()))


def check_ffmpeg(r: DoctorReport) -> dict | None:
    from cutroom.system.ffmpeg import MIN_FFMPEG_MAJOR, OPTIONAL_FILTERS, ffmpeg_info

    try:
        info = ffmpeg_info()
    except CutroomError as e:
        r.add("ffmpeg", "fail", e.message, e.fix)
        return None
    if info.major is not None and info.major < MIN_FFMPEG_MAJOR:
        r.add("ffmpeg", "fail", f"FFmpeg {info.version} is too old (need {MIN_FFMPEG_MAJOR}+).",
              "Re-run setup to install a current FFmpeg.")
    elif info.missing_required_filters or info.missing_required_encoders:
        r.add("ffmpeg", "fail",
              f"FFmpeg {info.version} lacks: {', '.join(info.missing_required_filters + info.missing_required_encoders)}",
              "Install a full FFmpeg build (setup does this; on Fedora use RPM Fusion's ffmpeg, not ffmpeg-free).")
    else:
        r.add("ffmpeg", "ok", f"FFmpeg {info.version} at {info.ffmpeg}")
    if info.missing_optional_filters:
        names = ", ".join(f"{f} ({OPTIONAL_FILTERS[f]})" for f in info.missing_optional_filters)
        r.add("ffmpeg optional filters", "warn", f"Missing: {names}",
              "Optional; features that need them fall back or are disabled.")
    return info.to_dict()


def check_hw_encoders(r: DoctorReport) -> list[str]:
    from cutroom.system.ffmpeg import working_hw_encoders

    try:
        hw = working_hw_encoders()
    except CutroomError:
        return []
    if hw:
        r.add("hardware encoding", "ok", f"Working: {', '.join(hw)} (previews); finals use libx264 for quality")
    else:
        r.add("hardware encoding", "ok", "None usable; software libx264 for everything (slower previews, same final quality)")
    return hw


def check_resources(r: DoctorReport, machine) -> None:
    ram = machine.ram_total_gb
    if ram < MIN_RAM_GB:
        r.add("memory", "fail", f"{ram} GB RAM (need {MIN_RAM_GB}+).", "Cutroom needs at least 8 GB RAM.")
    elif ram < RECOMMENDED_RAM_GB - 1:
        r.add("memory", "warn", f"{ram} GB RAM; smaller models and lower concurrency will be used.")
    else:
        r.add("memory", "ok", f"{ram} GB RAM ({machine.ram_available_gb} GB free); "
              f"{machine.cpu.physical_cores} cores / {machine.cpu.logical_cores} threads ({machine.cpu.model})")
    free = machine.disk_free_gb
    if free is None:
        r.add("disk", "warn", f"Could not read free space at {machine.disk_path}")
    elif free < MIN_DISK_GB:
        r.add("disk", "warn", f"{free} GB free at {machine.disk_path} (recommend {MIN_DISK_GB}+ GB).",
              "Free space, or set CUTROOM_JOBS_DIR to a larger drive. Copies, proxies and renders need room.")
    else:
        r.add("disk", "ok", f"{free} GB free at {machine.disk_path}")


def check_gpu(r: DoctorReport, machine) -> int:
    from cutroom.system.detect import cuda_device_count

    cuda = cuda_device_count()
    names = "; ".join(f"{g.name}" + (f" {g.vram_gb} GB" if g.vram_gb else "") for g in machine.gpus) or "none detected"
    if machine.has_nvidia and cuda == 0:
        r.add("gpu", "warn", f"{names}; CUDA not usable by CTranslate2, transcription runs on CPU.",
              "Update the NVIDIA driver and re-run setup (it installs cuBLAS/cuDNN wheels for NVIDIA machines).")
    elif cuda:
        r.add("gpu", "ok", f"{names}; CUDA devices usable for transcription: {cuda}")
    elif machine.is_apple_silicon:
        r.add("gpu", "ok", f"{names}; transcription runs on the CPU cores (fast on Apple Silicon); "
              "VideoToolbox handles video encoding")
    else:
        r.add("gpu", "ok", f"{names}; CPU-only mode")
    return cuda


def check_node(r: DoctorReport) -> None:
    node = shutil.which("node")
    if not node:
        r.add("node", "fail", "Node.js not found (needed for captions and motion graphics).",
              "Re-run setup, or install Node.js LTS (20+) from nodejs.org.")
        return
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    m = re.match(r"v(\d+)", out)
    if not m or int(m.group(1)) < MIN_NODE_MAJOR:
        r.add("node", "fail", f"Node.js {out or '?'} is too old (need {MIN_NODE_MAJOR}+).", "Install Node.js LTS.")
    else:
        r.add("node", "ok", f"Node.js {out}")
    from cutroom.graphics.remotion import graphics_dir

    g = graphics_dir()
    if (g / "node_modules" / "@remotion" / "renderer").is_dir():
        version = json.loads((g / "node_modules" / "remotion" / "package.json").read_text()).get("version")
        r.add("graphics package", "ok", f"Remotion {version} installed in {g}")
    else:
        r.add("graphics package", "fail", "Remotion is not installed.", f"Run `npm ci` in {g} (setup does this).")


def check_caption_render(r: DoctorReport) -> None:
    """Render 10 caption frames with Remotion and check the transparent PNGs really contain text."""
    import cv2

    from cutroom.graphics.remotion import render_overlay
    from cutroom.style import load_style

    style = load_style()
    from cutroom.captions.plan import resolved_style

    st = resolved_style(style["captions"]["styles"]["bold-karaoke"], 320, 180)
    line = [{"text": "Doctor", "from": 0, "to": 5, "emph": False}, {"text": "check", "from": 5, "to": 10, "emph": True}]
    props = {"width": 320, "height": 180, "fps": 10, "durationInFrames": 10, "style": st,
             "safe": style["captions"]["safe_zones"]["youtube_long"],
             "cues": [{"id": "q0", "from": 0, "to": 10, "placement": "bottom", "scrim": False, "lines": [line]}]}
    t0 = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="cutroom-gfx-") as td:
            out = Path(td) / "frames"
            render_overlay(props, out, concurrency=1, scratch=Path(td))
            img = cv2.imread(str(sorted(out.glob("*.png"))[5]), cv2.IMREAD_UNCHANGED)
            drawn = img is not None and img.shape[2] == 4 and int((img[:, :, 3] > 64).sum()) > 50
    except CutroomError as e:
        r.add("caption render", "fail", e.message, e.fix)
        return
    if drawn:
        r.add("caption render", "ok", f"Remotion drew transparent caption frames ({time.perf_counter() - t0:.1f}s incl. bundling)")
    else:
        r.add("caption render", "fail", "Remotion ran but the frames are empty.",
              "Check fonts/browser; run `npx remotion browser ensure` in graphics/.")


# --------------------------------------------------------------------------- ASR selection


class _PeakRSS:
    """Sample this process's RSS in the background to measure a model's peak memory."""

    def __init__(self) -> None:
        self.peak = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        p = psutil.Process()
        while not self._stop.is_set():
            self.peak = max(self.peak, p.memory_info().rss)
            time.sleep(0.05)

    def __enter__(self) -> "_PeakRSS":
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._t.join()


def keeps_stumble(text: str, phrase: str) -> bool:
    from cutroom.perception.textnorm import normalize

    return normalize(phrase) in normalize(text)


@dataclass
class BenchItem:
    """One benchmark clip: its 16 kHz WAV, reference text and what to score on it."""
    name: str
    audio: Path
    reference: str
    sentences: list[dict] | None = None   # exact sentence timings (synthetic clip only)
    stumble: str | None = None            # a disfluency the transcript must keep


def benchmark_asr(models: list[str], base, items: list[BenchItem],
                  log: Callable[[str], None] = lambda s: None) -> list[dict]:
    """Run each candidate on every clip; report speed, memory, words, timing and stumbles."""
    from cutroom.perception import transcribe as tr
    from cutroom.perception.models import ensure_model
    from cutroom.perception.textnorm import word_error_rate

    results = []
    for name in models:
        settings = base.model_copy(update={"model": name})
        log(f"benchmarking {name} ...")
        try:
            ensure_model(name)
            tr._MODEL_CACHE.clear()
            gc.collect()
            row: dict = {"model": name}
            elapsed = audio_s = 0.0
            with _PeakRSS() as rss:
                t0 = time.perf_counter()
                tr._load_model(settings)
                row["load_s"] = round(time.perf_counter() - t0, 2)
                tr.transcribe_audio(items[0].audio, settings)  # warm-up: first call pays one-off costs
                for it in items:
                    t = tr.transcribe_audio(it.audio, settings)
                    elapsed += t.elapsed_s
                    audio_s += t.duration
                    row[f"wer_{it.name}"] = round(word_error_rate(it.reference, t.text), 4)
                    if it.sentences:
                        row["timing_err_max_s"] = round(max(boundary_errors(t, it.sentences)), 3)
                    if it.stumble:
                        row[f"stumble_{it.name}"] = keeps_stumble(t.text, it.stumble)
            row["rtf"] = round(elapsed / audio_s, 3)
            row["wer"] = max(v for k, v in row.items() if k.startswith("wer_"))
            stumbles = [v for k, v in row.items() if k.startswith("stumble_")]
            if stumbles:
                row["keeps_stumble"] = all(stumbles)
            row["peak_rss_gb"] = round(rss.peak / 1024**3, 2)
            results.append(row)
        except CutroomError as e:
            results.append({"model": name, "error": e.message})
        finally:
            tr._MODEL_CACHE.clear()
            gc.collect()
    return results


def select_asr_model(results: list[dict], budget: float = ASR_RTF_BUDGET) -> tuple[str | None, str]:
    """Choose among candidates that pass the speed budget and the word/timing gates.

    Editing needs a transcript that is faithful (keeps stumbles like "we are, we are",
    so they can be cut) and well-timed (cuts and captions hang off word times). So:
    1. prefer candidates that kept the test clip's stumble;
    2. then the most accurate word timing (within 50 ms counts as a tie);
    3. then general accuracy (quality rank), then speed.
    """
    from cutroom.perception.models import ASR_MODELS

    pool = [r for r in results if "error" not in r and r["rtf"] <= budget and r["wer"] <= SMOKE_MAX_WER
            and r.get("timing_err_max_s", 0) <= SMOKE_MAX_BOUNDARY_ERR_S]
    if not pool:
        return None, (f"no candidate met RTF <= {budget}, WER <= {SMOKE_MAX_WER} and timing error "
                      f"<= {SMOKE_MAX_BOUNDARY_ERR_S}s")
    faithful = [r for r in pool if r.get("keeps_stumble", True)]
    pool = faithful or pool
    best_t = min(r.get("timing_err_max_s", 0) for r in pool)
    pool = [r for r in pool if r.get("timing_err_max_s", 0) <= best_t + 0.05]
    best = max(pool, key=lambda r: (ASR_MODELS[r["model"]].quality_rank, -r["rtf"]))
    parts = [f"RTF {best['rtf']} (<= {budget})", f"WER {best['wer']:.1%}"]
    if "timing_err_max_s" in best:
        parts.append(f"max timing error {best['timing_err_max_s'] * 1000:.0f} ms")
    if "keeps_stumble" in best:
        parts.append("keeps stumbles" if best["keeps_stumble"] else "tidies stumbles (no candidate kept them)")
    parts.append(f"{best['peak_rss_gb']} GB peak RAM")
    return best["model"], ", ".join(parts)


# --------------------------------------------------------------------------- smoke test


def sample_paths() -> tuple[Path, dict]:
    """Synthetic clip with exact ground truth (words, sentence timings, a stumble, a retake)."""
    mp4 = samples_dir() / "doctor_sample.mp4"
    meta = json.loads((samples_dir() / "doctor_sample.json").read_text(encoding="utf-8"))
    return mp4, meta


def real_sample_paths() -> tuple[Path, dict]:
    """13.5 s of public-domain natural speech with a real stumble."""
    audio = samples_dir() / "real_speech_stumble.opus"
    meta = json.loads((samples_dir() / "real_speech_stumble.json").read_text(encoding="utf-8"))
    return audio, meta


def _assign_sentence(word, sentences: list[dict]) -> int:
    """Index of the sentence a word overlaps most (nearest one if it overlaps none)."""
    def overlap(s):
        return min(word.end, s["end"]) - max(word.start, s["start"])

    def distance(s):
        return max(s["start"] - word.end, word.start - s["end"], 0.0)

    return max(range(len(sentences)), key=lambda i: (overlap(sentences[i]), -distance(sentences[i])))


def boundary_errors(transcript, sentences: list[dict]) -> list[float]:
    """|start| and |end| error (seconds) of each reference sentence vs the words assigned to it."""
    groups: dict[int, list] = {i: [] for i in range(len(sentences))}
    for w in transcript.words:
        groups[_assign_sentence(w, sentences)].append(w)
    errs = []
    for i, s in enumerate(sentences):
        ws = groups[i]
        if not ws:
            errs.append(float("inf"))
            continue
        errs += [abs(ws[0].start - s["start"]), abs(ws[-1].end - s["end"])]
    return errs


def run_smoke_test(asr, log: Callable[[str], None] = lambda s: None) -> dict:
    """Ingest → proxy → extract audio → transcribe the bundled sample in a throwaway job."""
    from cutroom.ingest.ingest import ingest
    from cutroom.ingest.probe import probe
    from cutroom.ingest.proxy import extract_audio, make_proxy
    from cutroom.job import Job
    from cutroom.perception.textnorm import word_error_rate
    from cutroom.perception.transcribe import transcribe_audio

    mp4, meta = sample_paths()
    result: dict = {"sample": str(mp4)}
    with tempfile.TemporaryDirectory(prefix="cutroom-doctor-") as td:
        job = Job.create("doctor-smoke-test", base=Path(td))
        log("ingest")
        res = ingest(job, [mp4], copy=True)
        if res[0].status != "added":
            raise CutroomError(f"Smoke test ingest failed: {res[0].message}")
        src = job.manifest.sources[0]
        log("proxy")
        p = make_proxy(job, src, short_side=540)
        pinfo = probe(job.abspath(src.proxy))
        result["proxy"] = {"size": p["size"], "fps": p["fps"], "duration_drift_s": p["duration_drift_s"]}
        log("extract audio")
        extract_audio(job, src)
        log(f"transcribe ({asr.model})")
        t = transcribe_audio(job.abspath(src.audio["asr"]), asr, source_id=src.id)
        wer = word_error_rate(meta["reference_text"], t.text)
        errs = boundary_errors(t, meta["sentences"])
        stumble = meta.get("stumble", {}).get("phrase")
        result["transcript"] = {"text": t.text, "wer": round(wer, 4), "rtf": t.rtf,
                                "keeps_stumble": keeps_stumble(t.text, stumble) if stumble else None,
                                "boundary_err_mean_s": round(sum(errs) / len(errs), 3),
                                "boundary_err_max_s": round(max(errs), 3), "words": len(t.words)}
        result["checks"] = {
            "proxy_not_upscaled": pinfo.primary_video.display_height == 360,
            "proxy_duration_ok": p["duration_drift_s"] < 0.1,
            "wer_ok": wer <= SMOKE_MAX_WER,
            "timing_ok": max(errs) <= SMOKE_MAX_BOUNDARY_ERR_S,
        }
    return result


# --------------------------------------------------------------------------- main entry


def run_doctor(*, quick: bool = False, select_asr: bool = False, asr_model: str | None = None,
               download: bool = True, save: bool = True,
               log: Callable[[str], None] = lambda s: None) -> DoctorReport:
    from cutroom.paths import machine_profile_path
    from cutroom.perception.models import ensure_model, get_spec, is_downloaded
    from cutroom.system.detect import detect_machine
    from cutroom.system.profile import MachineProfile, derive_profile, save_profile

    r = DoctorReport()
    log("detecting machine")
    machine = detect_machine()
    check_platform(r, machine)
    check_python(r, machine)
    check_packages(r)
    check_resources(r, machine)
    log("checking FFmpeg")
    ff = check_ffmpeg(r)
    hw = check_hw_encoders(r) if ff else []
    cuda = check_gpu(r, machine)
    check_node(r)

    profile = derive_profile(machine, ff, hw, cuda)
    # Keep an earlier benchmarked ASR choice unless asked to re-select.
    old = None
    if machine_profile_path().is_file():
        try:
            old = MachineProfile.model_validate_json(machine_profile_path().read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            old = None
    if old and old.asr.selection.get("method") in ("benchmark", "user") and not select_asr and not asr_model:
        profile.asr = old.asr

    if asr_model:
        get_spec(asr_model)
        profile.asr = profile.asr.model_copy(update={"model": asr_model, "selection": {
            "method": "user", "reason": "chosen with --asr-model"}})
    elif select_asr and ff:
        from cutroom.system.profile import CPU_ASR_CANDIDATES

        mp4, meta = sample_paths()
        real_audio, real_meta = real_sample_paths()
        candidates = [profile.asr.model] if profile.asr.device == "cuda" else CPU_ASR_CANDIDATES
        with tempfile.TemporaryDirectory(prefix="cutroom-bench-") as td:
            from cutroom.system.ffmpeg import run_ffmpeg

            items = []
            for name, src, m in (("synthetic", mp4, meta), ("real", real_audio, real_meta)):
                wav = Path(td) / f"{name}.wav"
                run_ffmpeg(["-y", "-i", str(src), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                           desc=f"benchmark audio ({name})")
                items.append(BenchItem(name, wav, m["reference_text"], m.get("sentences"),
                                       (m.get("stumble") or {}).get("phrase")))
            bench = benchmark_asr(candidates, profile.asr, items, log=log)
        r.asr_benchmark = bench
        chosen, why = select_asr_model(bench)
        if chosen:
            profile.asr = profile.asr.model_copy(update={"model": chosen, "selection": {
                "method": "benchmark", "reason": why, "results": bench, "rtf_budget": ASR_RTF_BUDGET}})
            r.add("asr selection", "ok", f"{chosen}: {why}")
        else:
            r.add("asr selection", "warn", f"Benchmark inconclusive ({why}); keeping {profile.asr.model}.")

    spec = get_spec(profile.asr.model)
    if is_downloaded(spec.name):
        r.add("asr model", "ok", f"{spec.name} ({spec.repo}@{spec.revision[:8]}, {spec.licence}) "
              f"on {profile.asr.device}/{profile.asr.compute_type}, {profile.asr.cpu_threads} threads")
    elif download:
        log(f"downloading {spec.name} (~{spec.download_mb} MB)")
        try:
            ensure_model(spec.name)
            r.add("asr model", "ok", f"{spec.name} downloaded ({spec.download_mb} MB, {spec.licence})")
        except CutroomError as e:
            r.add("asr model", "fail", e.message, e.fix)
    else:
        r.add("asr model", "fail", f"{spec.name} is not downloaded.", f"Run `cutroom models pull {spec.name}`.")

    from cutroom.perception.models import VISION_MODELS, ensure_file_model

    for name, vspec in VISION_MODELS.items():
        try:
            ensure_file_model(name) if download else None
            r.add(f"vision model {name}", "ok", f"{vspec.filename} ({vspec.licence}, checksum verified)")
        except CutroomError as e:
            r.add(f"vision model {name}", "warn", e.message, e.fix)

    can_smoke = ff is not None and is_downloaded(spec.name) and r.ok
    if quick:
        r.add("smoke test", "skip", "Skipped (--quick).")
    elif not can_smoke:
        r.add("smoke test", "skip", "Skipped because earlier checks failed.", "Fix the failures above and re-run.")
    else:
        log("caption render (Remotion)")
        check_caption_render(r)
        try:
            smoke = run_smoke_test(profile.asr, log=log)
            r.smoke = smoke
            failed = [k for k, v in smoke["checks"].items() if not v]
            tr = smoke["transcript"]
            detail = (f"ingest → proxy {smoke['proxy']['size']} → transcribe: WER {tr['wer']:.1%}, "
                      f"sentence timing error mean {tr['boundary_err_mean_s'] * 1000:.0f} ms / "
                      f"max {tr['boundary_err_max_s'] * 1000:.0f} ms, RTF {tr['rtf']}")
            if failed:
                r.add("smoke test", "fail", f"{detail}; failed: {', '.join(failed)}",
                      "Run `cutroom doctor --select-asr` to try another model, or report the output.")
            else:
                r.add("smoke test", "ok", detail)
            if tr.get("keeps_stumble") is False:
                r.add("verbatim", "warn", f"{profile.asr.model} tidied away the test clip's stumble "
                      "('we are, we are'); stumbles in real footage may be missed.",
                      "Run `cutroom doctor --select-asr`, or rely on Phase 1's acoustic stumble check.")
            profile.asr.selection.setdefault("smoke_test", tr | {"text": None})
        except CutroomError as e:
            r.add("smoke test", "fail", e.message, e.fix)

    r.profile = json.loads(profile.model_dump_json())
    if save and r.ok:
        save_profile(profile)
    return r
