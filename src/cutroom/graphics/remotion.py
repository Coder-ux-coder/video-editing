"""Run the Remotion package in graphics/ to render transparent overlay frames.

Python decides *what* to draw (props JSON); Remotion draws it frame by frame in headless
Chrome; FFmpeg composites the PNG frames onto the video during the normal render pass.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from cutroom.errors import CutroomError, ToolMissingError
from cutroom.paths import project_root

MIN_NODE_MAJOR = 18


def graphics_dir() -> Path:
    return project_root() / "graphics"


def node_path() -> Path:
    node = shutil.which("node")
    if not node:
        raise ToolMissingError("Node.js is not installed (needed for captions and motion graphics).",
                               fix="Re-run setup, or install Node.js LTS from nodejs.org.")
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    m = re.match(r"v(\d+)", out)
    if not m or int(m.group(1)) < MIN_NODE_MAJOR:
        raise ToolMissingError(f"Node.js {out} is too old (need {MIN_NODE_MAJOR}+).", fix="Install Node.js LTS.")
    return Path(node)


def ensure_installed() -> Path:
    """graphics/node_modules must exist (setup runs `npm ci`)."""
    g = graphics_dir()
    if not (g / "node_modules" / "@remotion" / "renderer").is_dir():
        raise ToolMissingError("The graphics package is not installed.",
                               fix=f"Run `npm ci` in {g} (setup does this), then retry.")
    return g


def browser_executable() -> str | None:
    """Chrome to render with: CUTROOM_BROWSER if set, else Remotion downloads its own."""
    b = os.environ.get("CUTROOM_BROWSER")
    return b if b and Path(b).is_file() else None


def render_overlay(props: dict, out_dir: Path, *, composition: str = "Captions", concurrency: int | None = None,
                   on_progress: Callable[[float], None] | None = None, scratch: Path | None = None) -> dict:
    """Render ``composition`` with ``props`` to ``out_dir`` as NNNN.png frames (transparent)."""
    g = ensure_installed()
    node = node_path()
    scratch = scratch or out_dir.parent
    props_path = scratch / f"{composition.lower()}_props.json"
    props_path.write_text(json.dumps(props), encoding="utf-8")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    cmd = [str(node), str(g / "render.mjs"), "--composition", composition, "--props", str(props_path),
           "--out", str(out_dir)]
    if concurrency:
        cmd += ["--concurrency", str(concurrency)]
    b = browser_executable()
    if b:
        cmd += ["--browser", b]
    proc = subprocess.Popen(cmd, cwd=g, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    result: dict = {}
    assert proc.stdout is not None
    for line in proc.stdout:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "progress" in msg and on_progress:
            on_progress(float(msg["progress"]))
        if msg.get("done") or msg.get("error"):
            result = msg
    stderr = proc.stderr.read() if proc.stderr else ""
    proc.wait()
    if proc.returncode != 0 or "error" in result:
        detail = (result.get("error") or stderr or "").strip()[-2000:]
        fix = "Check Node.js and `npm ci` in graphics/."
        if "browser" in detail.lower() or "chrome" in detail.lower():
            fix = ("Remotion could not start Chrome. Run `npx remotion browser ensure` in graphics/, "
                   "or set CUTROOM_BROWSER to a Chrome Headless Shell executable.")
        raise CutroomError(f"Remotion render of {composition} failed.", fix=fix, details=detail)
    return result


def frame_pattern(out_dir: Path) -> tuple[str, int]:
    """FFmpeg image2 pattern for Remotion's zero-padded frames, and the padding width."""
    first = next(iter(sorted(out_dir.glob("*.png"))), None)
    if first is None:
        raise CutroomError(f"No overlay frames in {out_dir}.", fix="Re-run the render.")
    width = len(first.stem)
    return str(out_dir / f"%0{width}d.png"), width
