"""Where Cutroom keeps things. All paths are pathlib-based and cross-platform.

Every location can be overridden with an environment variable, so large client
footage and models can live on another drive (useful on Windows, where the
repository may sit on a small system disk or under a long path).
"""

from __future__ import annotations

import os
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent


def project_root() -> Path:
    """The repository root (the folder that holds ``pyproject.toml``)."""
    env = os.environ.get("CUTROOM_HOME")
    if env:
        return Path(env).expanduser().resolve()
    # Editable install: src/cutroom/ -> repository root two levels up.
    candidate = _PACKAGE_DIR.parent.parent
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return Path.cwd().resolve()


def _dir_from_env(var: str, default: Path) -> Path:
    value = os.environ.get(var)
    return Path(value).expanduser().resolve() if value else default


def jobs_dir() -> Path:
    return _dir_from_env("CUTROOM_JOBS_DIR", project_root() / "jobs")


def models_dir() -> Path:
    return _dir_from_env("CUTROOM_MODELS_DIR", project_root() / "models")


def config_dir() -> Path:
    return project_root() / "config"


def machine_profile_path() -> Path:
    return config_dir() / "machine.json"


def assets_dir() -> Path:
    return project_root() / "assets"


def samples_dir() -> Path:
    return assets_dir() / "samples"


def docs_dir() -> Path:
    return project_root() / "docs"


def tools_dir() -> Path:
    """Folder for portable binaries setup may download (e.g. FFmpeg without admin rights)."""
    return project_root() / "tools"


def rel_or_abs(path: Path, base: Path) -> str:
    """Store ``path`` relative to ``base`` when it is inside it, else absolute.

    Always uses forward slashes so manifests are identical across OSes.
    """
    path = Path(path).resolve()
    try:
        return path.relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_stored(stored: str, base: Path) -> Path:
    """Inverse of :func:`rel_or_abs`."""
    p = Path(stored)
    return p if p.is_absolute() else (base / p)
