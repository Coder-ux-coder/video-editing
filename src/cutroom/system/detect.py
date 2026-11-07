"""Detect the operating system, CPU, memory, disk and GPU of the current machine.

Everything here is best-effort and never raises: missing information is
reported as ``None`` so `cutroom doctor` can still produce a full report.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import psutil


def _run(cmd: list[str], timeout: float = 10) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


@dataclass
class OSInfo:
    system: str          # "Windows" | "Darwin" | "Linux"
    pretty: str          # human-readable name/version
    release: str
    arch: str            # "x86_64" | "arm64" | ...
    is_wsl: bool = False
    is_container: bool = False
    windows_long_paths: bool | None = None


@dataclass
class CPUInfo:
    model: str | None
    physical_cores: int | None
    logical_cores: int | None
    avx2: bool | None = None


@dataclass
class GPUInfo:
    vendor: str          # "nvidia" | "apple" | "intel" | "amd" | "unknown"
    name: str
    vram_gb: float | None = None
    driver: str | None = None


@dataclass
class MachineInfo:
    os: OSInfo
    cpu: CPUInfo
    ram_total_gb: float
    ram_available_gb: float
    disk_free_gb: float | None
    disk_path: str
    gpus: list[GPUInfo] = field(default_factory=list)
    python: str = field(default_factory=lambda: platform.python_version())
    python_executable: str = field(default_factory=lambda: sys.executable)
    in_venv: bool = field(default_factory=lambda: sys.prefix != sys.base_prefix)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def has_nvidia(self) -> bool:
        return any(g.vendor == "nvidia" for g in self.gpus)

    @property
    def is_apple_silicon(self) -> bool:
        return self.os.system == "Darwin" and self.os.arch in ("arm64", "aarch64")


# --------------------------------------------------------------------------- OS


def detect_os() -> OSInfo:
    system = platform.system()
    arch = platform.machine().lower()
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(arch, arch)
    pretty = f"{system} {platform.release()}"
    is_wsl = False
    is_container = False
    long_paths = None
    if system == "Linux":
        osr = Path("/etc/os-release")
        if osr.is_file():
            m = re.search(r'^PRETTY_NAME="?([^"\n]+)', osr.read_text(errors="replace"), re.M)
            if m:
                pretty = m.group(1)
        is_wsl = "microsoft" in platform.release().lower()
        is_container = Path("/.dockerenv").exists() or bool(os.environ.get("container"))
    elif system == "Darwin":
        ver = platform.mac_ver()[0]
        pretty = f"macOS {ver}" if ver else "macOS"
    elif system == "Windows":
        pretty = f"Windows {platform.release()} ({platform.version()})"
        long_paths = _windows_long_paths_enabled()
    return OSInfo(system=system, pretty=pretty, release=platform.release(), arch=arch,
                  is_wsl=is_wsl, is_container=is_container, windows_long_paths=long_paths)


def _windows_long_paths_enabled() -> bool | None:
    try:
        import winreg  # type: ignore[import-not-found]

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem")
        value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
        return bool(value)
    except Exception:  # noqa: BLE001 - registry access is best-effort
        return None


# --------------------------------------------------------------------------- CPU


def detect_cpu() -> CPUInfo:
    system = platform.system()
    model = None
    avx2 = None
    if system == "Linux":
        try:
            text = Path("/proc/cpuinfo").read_text(errors="replace")
            m = re.search(r"^model name\s*:\s*(.+)$", text, re.M)
            model = m.group(1).strip() if m else None
            flags = re.search(r"^flags\s*:\s*(.+)$", text, re.M)
            avx2 = ("avx2" in flags.group(1).split()) if flags else None
        except OSError:
            pass
    elif system == "Darwin":
        model = (_run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "").strip() or None
    elif system == "Windows":
        model = platform.processor() or None
    return CPUInfo(model=model or platform.processor() or None,
                   physical_cores=psutil.cpu_count(logical=False),
                   logical_cores=psutil.cpu_count(logical=True),
                   avx2=avx2)


# --------------------------------------------------------------------------- GPU


def detect_gpus() -> list[GPUInfo]:
    gpus: list[GPUInfo] = []
    gpus += _nvidia_gpus()
    system = platform.system()
    if system == "Darwin" and platform.machine().lower() in ("arm64", "aarch64"):
        chip = (_run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple Silicon").strip()
        gpus.append(GPUInfo(vendor="apple", name=f"{chip} GPU (unified memory)"))
    elif system == "Windows":
        gpus += [g for g in _windows_gpus() if g.vendor != "nvidia" or not gpus]
    elif system == "Linux":
        gpus += _linux_other_gpus()
    return gpus


def _nvidia_gpus() -> list[GPUInfo]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    out = _run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    gpus = []
    for line in (out or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            try:
                vram = round(float(parts[1]) / 1024, 1)
            except ValueError:
                vram = None
            gpus.append(GPUInfo(vendor="nvidia", name=parts[0], vram_gb=vram, driver=parts[2]))
    return gpus


def _vendor_from_name(name: str) -> str:
    n = name.lower()
    for vendor, keys in (("nvidia", ("nvidia", "geforce", "quadro", "rtx")),
                         ("amd", ("amd", "radeon")),
                         ("intel", ("intel", "iris", "uhd", "arc"))):
        if any(k in n for k in keys):
            return vendor
    return "unknown"


def _windows_gpus() -> list[GPUInfo]:
    out = _run(["powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"], timeout=20)
    return [GPUInfo(vendor=_vendor_from_name(n), name=n.strip())
            for n in (out or "").splitlines() if n.strip()]


def _linux_other_gpus() -> list[GPUInfo]:
    gpus = []
    out = _run(["lspci"]) or ""
    for line in out.splitlines():
        if re.search(r"VGA|3D controller|Display controller", line):
            name = line.split(":", 2)[-1].strip()
            vendor = _vendor_from_name(name)
            if vendor != "nvidia":  # NVIDIA already covered by nvidia-smi
                gpus.append(GPUInfo(vendor=vendor, name=name))
    if not gpus and Path("/dev/dri/renderD128").exists():
        gpus.append(GPUInfo(vendor="unknown", name="DRM render node /dev/dri/renderD128"))
    return gpus


def cuda_device_count() -> int:
    """GPUs CTranslate2 (the faster-whisper backend) can actually use."""
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001 - absence of CUDA is normal
        return 0


# --------------------------------------------------------------------------- all


def detect_machine(disk_path: Path | None = None) -> MachineInfo:
    from cutroom.paths import jobs_dir

    disk_path = Path(disk_path or jobs_dir())
    probe = disk_path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = round(shutil.disk_usage(probe).free / 1024**3, 1)
    except OSError:
        free = None
    vm = psutil.virtual_memory()
    return MachineInfo(
        os=detect_os(),
        cpu=detect_cpu(),
        ram_total_gb=round(vm.total / 1024**3, 1),
        ram_available_gb=round(vm.available / 1024**3, 1),
        disk_free_gb=free,
        disk_path=str(disk_path),
        gpus=detect_gpus(),
    )
