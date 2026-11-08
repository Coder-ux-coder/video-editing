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
                   on_progress: Callable[[float], None] | None = None, scratch: Path | None = None,
                   frames: list[int] | None = None, frame_range: tuple[int, int] | None = None,
                   keep_existing: bool = False, frame_map: list[int] | None = None) -> dict:
    """Render ``composition`` with ``props`` to ``out_dir`` as NNNN.png frames (transparent).
    With ``frames``, render only those frames as stills named <frame>.png. With ``frame_map``
    (Overlay only), output frame i is composition frame frame_map[i], drawn in one pass."""
    if frame_map is not None:
        props = {**props, "frameMap": [int(f) for f in frame_map]}
        frame_range = (0, len(frame_map) - 1)
    g = ensure_installed()
    node = node_path()
    scratch = scratch or out_dir.parent
    props_path = scratch / f"{composition.lower()}_props.json"
    props_path.write_text(json.dumps(props), encoding="utf-8")
    if out_dir.exists() and not keep_existing:
        shutil.rmtree(out_dir)
    cmd = [str(node), str(g / "render.mjs"), "--composition", composition, "--props", str(props_path),
           "--out", str(out_dir)]
    if concurrency:
        cmd += ["--concurrency", str(concurrency)]
    if frames:
        cmd += ["--frames", ",".join(str(int(f)) for f in frames)]
    if frame_range:
        cmd += ["--from", str(int(frame_range[0])), "--to", str(int(frame_range[1]))]
    b = browser_executable()
    if b:
        cmd += ["--browser", b]
    from cutroom.system.procs import popen_group, stop_tree

    proc = popen_group(cmd, cwd=g, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       encoding="utf-8", errors="replace")
    result: dict = {}
    assert proc.stdout is not None
    try:
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
    except BaseException:                                     # Ctrl-C: stop Node and its Chrome too
        stop_tree(proc)
        raise
    if proc.returncode != 0 or "error" in result:
        detail = (result.get("error") or stderr or "").strip()[-2000:]
        fix = "Check Node.js and `npm ci` in graphics/."
        if "browser" in detail.lower() or "chrome" in detail.lower():
            fix = ("Remotion could not start Chrome. Run `npx remotion browser ensure` in graphics/, "
                   "or set CUTROOM_BROWSER to a Chrome Headless Shell executable.")
        raise CutroomError(f"Remotion render of {composition} failed.", fix=fix, details=detail)
    result["opaque_frames_fixed"] = normalize_alpha(out_dir)
    return result


def caption_states(props: dict, scratch: Path) -> list:
    """Per overlay frame: None, or [cue index, caption animation state] (caption_states.mjs)."""
    g = ensure_installed()
    scratch.mkdir(parents=True, exist_ok=True)
    src, dst = scratch / "caption_state_props.json", scratch / "caption_states.json"
    src.write_text(json.dumps(props), encoding="utf-8")
    r = subprocess.run([str(node_path()), str(g / "caption_states.mjs"), "--props", str(src), "--out", str(dst)],
                       cwd=g, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise CutroomError("Could not compute caption states.", details=(r.stderr or "")[-2000:])
    return json.loads(dst.read_text(encoding="utf-8"))


def normalize_alpha(out_dir: Path) -> int:
    """Chrome writes a fully opaque frame (a dip to black at its peak) as an RGB PNG. In a
    sequence of RGBA frames that would change FFmpeg's input pixel format mid-stream, so such
    frames are rewritten with an opaque alpha channel. Returns how many were rewritten."""
    import cv2
    import numpy as np

    fixed = 0
    for p in out_dir.glob("*.png"):
        with p.open("rb") as fh:
            head = fh.read(26)
        if len(head) == 26 and head[25] != 6:      # PNG IHDR colour type 6 = RGBA
            img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
            if img.ndim == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            if img.shape[2] == 3:
                img = np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])
            cv2.imwrite(str(p), img)
            fixed += 1
    return fixed


def frame_pattern(out_dir: Path) -> tuple[str, int]:
    """FFmpeg image2 pattern for Remotion's zero-padded frames, and the padding width."""
    first = next(iter(sorted(out_dir.glob("*.png"))), None)
    if first is None:
        raise CutroomError(f"No overlay frames in {out_dir}.", fix="Re-run the render.")
    width = len(first.stem)
    return str(out_dir / f"%0{width}d.png"), width
