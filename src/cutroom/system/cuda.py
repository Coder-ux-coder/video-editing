"""Make pip-installed NVIDIA libraries (cuBLAS, cuDNN) visible to CTranslate2.

faster-whisper on an NVIDIA GPU needs cuBLAS 12 and cuDNN 9. Setup installs
them as pip wheels (``nvidia-cublas-cu12``, ``nvidia-cudnn-cu12``) so nobody has
to install the CUDA toolkit by hand; this module loads them before the model.
"""

from __future__ import annotations

import ctypes
import os
import platform
from pathlib import Path

_loaded: list[str] | None = None


def _nvidia_dirs() -> list[Path]:
    try:
        import nvidia  # type: ignore[import-not-found]  # namespace package from the wheels
    except ImportError:
        return []
    roots = [Path(p) for p in getattr(nvidia, "__path__", [])]
    sub = "bin" if platform.system() == "Windows" else "lib"
    return [r / pkg / sub for r in roots for pkg in ("cublas", "cudnn") if (r / pkg / sub).is_dir()]


def preload_cuda_libraries() -> list[str]:
    """Best effort; returns the libraries/directories it registered."""
    global _loaded
    if _loaded is not None:
        return _loaded
    loaded: list[str] = []
    for d in _nvidia_dirs():
        if platform.system() == "Windows":
            os.add_dll_directory(str(d))  # type: ignore[attr-defined]
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            loaded.append(str(d))
            continue
        # cuBLASLt before cuBLAS; cuDNN's main library before its sub-libraries.
        libs = sorted(d.glob("lib*.so*"), key=lambda p: (("Lt" not in p.name), ("_" in p.name), p.name))
        for lib in libs:
            try:
                ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
                loaded.append(lib.name)
            except OSError:
                continue
    _loaded = loaded
    return loaded
