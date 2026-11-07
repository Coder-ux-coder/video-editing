#!/usr/bin/env python3
"""Cutroom setup. Standard library only, so it runs before anything is installed.

Steps (each is checked first and skipped when already done):
  1. FFmpeg: use an existing full build, else install one
     (Windows: winget, or a portable build into tools/ffmpeg without admin rights;
      macOS: Homebrew; Linux: the system package manager, or a portable build).
  2. Python: create .venv with Python 3.12 and install the pinned requirements.lock
     (plus NVIDIA cuBLAS/cuDNN wheels when an NVIDIA GPU is present).
  3. Node.js: check for 18+ (needed for motion graphics from Phase 3); offer to install.
  4. Run `cutroom doctor --select-asr`: benchmarks speech models on this machine,
     downloads the chosen one, and runs an end-to-end test transcription.

Usage (normally via ./setup.sh or .\\setup.ps1):
  python setup/bootstrap.py [--yes] [--dry-run] [--skip-ffmpeg] [--skip-node] [--skip-doctor]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
TOOLS = ROOT / "tools"
SYSTEM = platform.system()
ARCH = {"amd64": "x86_64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
# Filters whose absence means "this is not a full FFmpeg build".
KEY_FILTERS = ["loudnorm", "silencedetect", "subtitles", "drawtext", "sidechaincompress", "afftdn"]
KEY_ENCODERS = ["libx264", "aac"]
BTBN_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"

ARGS: argparse.Namespace


# --------------------------------------------------------------------------- helpers


def say(msg: str) -> None:
    print(f"[setup] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[setup] WARNING: {msg}", flush=True)


def run(cmd: list[str], *, check: bool = True, **kw) -> subprocess.CompletedProcess:
    say("$ " + " ".join(str(c) for c in cmd))
    if ARGS.dry_run:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run([str(c) for c in cmd], check=check, **kw)


def ask(question: str) -> bool:
    if ARGS.yes or ARGS.dry_run:
        return True
    try:
        return input(f"[setup] {question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def capture(cmd: list[str]) -> str:
    try:
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        return r.stdout + r.stderr
    except (OSError, subprocess.TimeoutExpired):
        return ""


def download(url: str, dest: Path) -> None:
    say(f"downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "cutroom-setup"})
    with urllib.request.urlopen(req, timeout=60) as r, dest.open("wb") as f:
        shutil.copyfileobj(r, f, length=1024 * 1024)


# --------------------------------------------------------------------------- FFmpeg


def ffmpeg_candidates() -> list[Path]:
    exe = "ffmpeg.exe" if SYSTEM == "Windows" else "ffmpeg"
    found = [p for p in (TOOLS / "ffmpeg" / "bin" / exe,) if p.is_file()]
    if SYSTEM == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        found += sorted(base.glob("Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe"), reverse=True) if base.is_dir() else []
    which = shutil.which("ffmpeg")
    if which:
        found.append(Path(which))
    return found


def ffmpeg_is_full(ffmpeg: Path) -> tuple[bool, str]:
    head = capture([ffmpeg, "-hide_banner", "-version"])
    m = re.search(r"ffmpeg version n?(\d+)\.", head)
    if not m or int(m.group(1)) < 6:
        return False, f"version too old or unreadable ({head.splitlines()[0] if head else 'no output'})"
    filters = capture([ffmpeg, "-hide_banner", "-filters"])
    encoders = capture([ffmpeg, "-hide_banner", "-encoders"])
    missing = [f for f in KEY_FILTERS if not re.search(rf"\s{f}\s", filters)]
    missing += [e for e in KEY_ENCODERS if not re.search(rf"\s{e}\s", encoders)]
    if missing:
        return False, "missing " + ", ".join(missing)
    return True, head.splitlines()[0]


def find_good_ffmpeg() -> Path | None:
    for c in ffmpeg_candidates():
        ok, why = ffmpeg_is_full(c)
        say(f"FFmpeg at {c}: {'OK, ' if ok else 'not usable, '}{why}")
        if ok:
            return c
    return None


def btbn_platform(system: str = SYSTEM, arch: str = ARCH) -> str | None:
    return {("Windows", "x86_64"): "win64", ("Windows", "arm64"): "winarm64",
            ("Linux", "x86_64"): "linux64", ("Linux", "arm64"): "linuxarm64"}.get((system, arch))


def pick_btbn_asset(assets: list[dict], plat: str, system: str = SYSTEM) -> str | None:
    """Newest *release-branch* GPL build (e.g. ffmpeg-n7.1-latest-win64-gpl-7.1.zip), not master."""
    ext = r"zip" if system == "Windows" else r"tar\.xz"
    pat = re.compile(rf"^ffmpeg-n(\d+)\.(\d+)-latest-{plat}-gpl-\d+\.\d+\.{ext}$")
    best = None
    for a in assets:
        m = pat.match(a.get("name", ""))
        if m:
            ver = (int(m.group(1)), int(m.group(2)))
            if best is None or ver > best[0]:
                best = (ver, a["browser_download_url"])
    return best[1] if best else None


def btbn_asset_url() -> str | None:
    """Portable FFmpeg from BtbN/FFmpeg-Builds (Windows/Linux, x86_64/arm64)."""
    plat = btbn_platform()
    if not plat:
        return None
    req = urllib.request.Request(BTBN_API, headers={"User-Agent": "cutroom-setup",
                                                    "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        assets = json.load(r).get("assets", [])
    return pick_btbn_asset(assets, plat)


def install_portable_ffmpeg() -> bool:
    try:
        url = btbn_asset_url()
    except Exception as e:  # noqa: BLE001
        warn(f"could not query portable FFmpeg builds: {e}")
        return False
    if not url:
        warn(f"no portable FFmpeg build for {SYSTEM}/{ARCH}")
        return False
    if ARGS.dry_run:
        say(f"would download {url} into {TOOLS / 'ffmpeg'}")
        return True
    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / url.rsplit("/", 1)[-1]
        download(url, archive)
        extract_dir = Path(td) / "x"
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(extract_dir)
        else:
            with tarfile.open(archive) as t:
                t.extractall(extract_dir, filter="data")
        bins = list(extract_dir.glob("*/bin"))
        if not bins:
            warn("unexpected archive layout")
            return False
        target = TOOLS / "ffmpeg"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(bins[0].parent, target)
        for exe in (target / "bin").iterdir():
            exe.chmod(0o755)
    say(f"portable FFmpeg installed in {target} (Cutroom finds it automatically)")
    return True


def install_ffmpeg() -> bool:
    if SYSTEM == "Windows":
        if shutil.which("winget") and ask("Install FFmpeg (Gyan full build, GPL, free) with winget?"):
            r = run(["winget", "install", "-e", "--id", "Gyan.FFmpeg", "--accept-source-agreements",
                     "--accept-package-agreements"], check=False)
            if r.returncode == 0:
                return True
        if ask("Download a portable FFmpeg into tools/ffmpeg (no admin rights needed)?"):
            return install_portable_ffmpeg()
        return False
    if SYSTEM == "Darwin":
        if shutil.which("brew"):
            if ask("Install FFmpeg with Homebrew (brew install ffmpeg)?"):
                return run(["brew", "install", "ffmpeg"], check=False).returncode == 0
            return False
        warn("Homebrew not found. Install it from https://brew.sh (free), then re-run setup.")
        return False
    # Linux
    sudo = [] if os.geteuid() == 0 else (["sudo"] if shutil.which("sudo") else None)
    if sudo is not None:
        if shutil.which("apt-get") and ask("Install FFmpeg with apt (sudo apt-get install ffmpeg)?"):
            run([*sudo, "apt-get", "update"], check=False)
            if run([*sudo, "apt-get", "install", "-y", "ffmpeg"], check=False).returncode == 0:
                return True
        elif shutil.which("pacman") and ask("Install FFmpeg with pacman?"):
            if run([*sudo, "pacman", "-S", "--needed", "--noconfirm", "ffmpeg"], check=False).returncode == 0:
                return True
        elif shutil.which("dnf"):
            warn("Fedora's 'ffmpeg-free' lacks x264. Enable RPM Fusion and install 'ffmpeg', "
                 "or accept the portable build below.")
    if ask("Download a portable FFmpeg into tools/ffmpeg (no root needed)?"):
        return install_portable_ffmpeg()
    return False


def step_ffmpeg() -> bool:
    say("== 1/4 FFmpeg ==")
    if find_good_ffmpeg():
        return True
    say("No full FFmpeg build found.")
    if not install_ffmpeg():
        warn("FFmpeg is required. Install a full build manually (https://ffmpeg.org/download.html) and re-run.")
        return False
    return ARGS.dry_run or find_good_ffmpeg() is not None


# --------------------------------------------------------------------------- Python


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if SYSTEM == "Windows" else "bin/python")


def has_nvidia() -> bool:
    smi = shutil.which("nvidia-smi")
    return bool(smi and capture([smi, "-L"]).strip().startswith("GPU"))


def step_python() -> bool:
    say("== 2/4 Python environment ==")
    if sys.version_info[:2] != (3, 12):
        warn(f"This is Python {platform.python_version()}; Cutroom needs 3.12. "
             "Run setup via ./setup.sh or .\\setup.ps1, which find or install Python 3.12.")
        return False
    if not venv_python().is_file():
        run([sys.executable, "-m", "venv", str(VENV)])
    py = venv_python()
    # Some venv creators (e.g. uv) leave pip out; ensurepip is a no-op when it is present.
    run([py, "-m", "ensurepip", "--upgrade"], check=False, capture_output=True)
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(ROOT / "requirements.lock")])
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--no-deps", "-e", str(ROOT)])
    if has_nvidia() and SYSTEM in ("Windows", "Linux"):
        say("NVIDIA GPU found: installing cuBLAS/cuDNN runtime wheels for GPU transcription")
        run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
             str(ROOT / "requirements-gpu.txt")], check=False)
    return True


# --------------------------------------------------------------------------- Node


def step_node() -> bool:
    say("== 3/4 Node.js (motion graphics, needed from Phase 3) ==")
    node = shutil.which("node")
    if node:
        out = capture([node, "--version"]).strip()
        m = re.match(r"v(\d+)", out)
        if m and int(m.group(1)) >= 18:
            say(f"Node.js {out} OK")
            return True
        warn(f"Node.js {out} is older than 18.")
    if SYSTEM == "Windows" and shutil.which("winget") and ask("Install Node.js LTS with winget?"):
        return run(["winget", "install", "-e", "--id", "OpenJS.NodeJS.LTS", "--accept-source-agreements",
                    "--accept-package-agreements"], check=False).returncode == 0
    if SYSTEM == "Darwin" and shutil.which("brew") and ask("Install Node.js with Homebrew?"):
        return run(["brew", "install", "node"], check=False).returncode == 0
    warn("Install Node.js LTS (free) from https://nodejs.org when you reach Phase 3. Not needed yet.")
    return True


# --------------------------------------------------------------------------- doctor


def step_doctor() -> bool:
    say("== 4/4 cutroom doctor (benchmarks speech models, runs a test transcription) ==")
    env = dict(os.environ, PYTHONUTF8="1")
    args = [venv_python(), "-m", "cutroom", "doctor"]
    if not ARGS.no_select_asr:
        args.append("--select-asr")
    r = run(args, check=False, env=env)
    return r.returncode == 0


def main() -> int:
    global ARGS
    p = argparse.ArgumentParser(description="Set up Cutroom on this machine.")
    p.add_argument("--yes", action="store_true", help="answer yes to every install prompt")
    p.add_argument("--dry-run", action="store_true", help="print what would happen, change nothing")
    p.add_argument("--skip-ffmpeg", action="store_true")
    p.add_argument("--skip-node", action="store_true")
    p.add_argument("--skip-doctor", action="store_true")
    p.add_argument("--no-select-asr", action="store_true", help="skip the speech-model benchmark")
    ARGS = p.parse_args()
    say(f"Cutroom setup on {SYSTEM} {platform.release()} ({ARCH}), Python {platform.python_version()}")
    ok = True
    if not ARGS.skip_ffmpeg:
        ok &= step_ffmpeg()
    ok &= step_python()
    if not ARGS.skip_node:
        ok &= step_node()
    if ok and not ARGS.skip_doctor:
        ok &= step_doctor()
    activate = r".venv\Scripts\activate" if SYSTEM == "Windows" else "source .venv/bin/activate"
    if ok:
        say(f"Done. Activate the environment with `{activate}`, then run `cutroom --help`.")
        return 0
    warn("Setup finished with problems; read the messages above, fix them, and re-run setup.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
