"""Load style files: the default style, deep-merged with an optional client style."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from cutroom.errors import CutroomError
from cutroom.paths import project_root


def styles_dir() -> Path:
    return project_root() / "styles"


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise CutroomError(f"Style file {path} is not valid YAML: {e}", fix="Fix the YAML syntax.") from e
    if not isinstance(data, dict):
        raise CutroomError(f"Style file {path} must contain a mapping.", fix="See styles/default.style.yaml.")
    return data


def load_style(name: str | None = None, client: str | None = None) -> dict[str, Any]:
    """``default`` merged with ``styles/<name>.style.yaml`` and/or the client's style."""
    style = _read(styles_dir() / "default.style.yaml")
    layers = []
    if name and name != "default":
        layers.append(styles_dir() / f"{name}.style.yaml")
    if client:
        layers.append(styles_dir() / "clients" / f"{client}.style.yaml")
    for path in layers:
        if path.is_file():
            style = _merge(style, _read(path))
        elif path.parent.name != "clients":
            raise CutroomError(f"Style '{name}' not found ({path}).", fix=f"Available: {', '.join(list_styles())}")
    return style


def timeline_style(tl) -> dict[str, Any]:
    """The style a timeline renders with: its named style plus its client theme."""
    return load_style(tl.meta.style, client=tl.meta.client_style or tl.meta.client)


def list_styles() -> list[str]:
    return sorted(p.name.removesuffix(".style.yaml") for p in styles_dir().glob("*.style.yaml"))


def editing(style: dict) -> dict[str, Any]:
    return style.get("editing", {})
