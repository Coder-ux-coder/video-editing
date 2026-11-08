"""Overlay frame cache: an overlay frame is drawn once, then reused.

Every overlay frame (graphics and burned-in captions, drawn by Remotion in headless Chrome) is a
pure function of what is on screen at that frame. Graphics are drawn inside Remotion Sequences,
so a graphic sees only its own local frame; captions show at most one cue, which depends only on
its words, the caption style and the frame relative to the cue's start. So a frame's key is

    hash(drawing code, output size, fps, theme, caption style)  +  for each item on screen,
    in drawing order: hash(item content without its position in time) + local frame

and two frames with the same key are pixel-identical. That makes three things free:

- frames with nothing on screen (one transparent frame, drawn once);
- frames identical within a render;
- frames an earlier render already drew: after a cut, everything after it moves in time but
  looks the same, so a new version only draws the frames that actually changed.

The cache lives in the job (``renders/.overlay-cache``) and is size-capped (oldest used first).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

# Components that read the composition's length (useVideoConfig().durationInFrames): their
# frames also depend on it. tests/test_framecache.py checks this set against the sources.
GLOBAL_LENGTH_COMPONENTS = {"ProgressBar"}

PNG_END = b"IEND\xaeB`\x82"
CACHE_DIRNAME = ".overlay-cache"
DEFAULT_MAX_GB = 3.0


def _h(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                        .encode("utf-8")).hexdigest()


def _find_cue(cues: list[dict], frame: int) -> dict | None:
    """The same binary search as Captions.jsx findCue."""
    lo, hi = 0, len(cues) - 1
    while lo <= hi:
        mid = (lo + hi) >> 1
        c = cues[mid]
        if frame < c["from"]:
            hi = mid - 1
        elif frame >= c["to"]:
            lo = mid + 1
        else:
            return c
    return None


def _cue_content(cue: dict) -> str:
    """A cue without its place in time: word timings relative to the cue's start."""
    a = cue["from"]
    lines = [[{**w, "from": w["from"] - a, "to": w["to"] - a} for w in line] for line in cue.get("lines", [])]
    rest = {k: v for k, v in cue.items() if k not in ("id", "from", "to", "lines")}
    return _h({"len": cue["to"] - a, "lines": lines, **rest})


def frame_keys(props: dict, code_key: str) -> list[str]:
    """One key per overlay frame (0 .. durationInFrames-1); equal keys mean identical pixels."""
    n = int(props["durationInFrames"])
    cap = props.get("captions") or None
    base = _h({"code": code_key, "w": props["width"], "h": props["height"], "fps": props["fps"],
               "theme": props.get("theme", {}),
               "cap": {"style": cap["style"], "safe": cap["safe"]} if cap and cap.get("cues") else None})
    # Drawing order as in Overlay.jsx: a stable sort by z.
    ordered = sorted(props.get("graphics", []), key=lambda g: g.get("z") or 0)
    gfx = []
    for g in ordered:
        dur = max(1, g["to"] - g["from"])
        content = {k: v for k, v in g.items() if k not in ("id", "from", "to")}
        content["dur"] = dur
        if g.get("component") in GLOBAL_LENGTH_COMPONENTS:
            content["composition_frames"] = n
        gfx.append((g["from"], g["from"] + dur, _h(content)))
    cues = cap.get("cues", []) if cap else []
    cue_h: dict[int, str] = {}
    keys = []
    for f in range(n):
        items = [(h, f - a) for a, b, h in gfx if a <= f < b]
        c = _find_cue(cues, f) if cues else None
        if c is not None:
            ch = cue_h.get(id(c)) or cue_h.setdefault(id(c), _cue_content(c))
            items.append((ch, f - c["from"]))
        keys.append(hashlib.sha1((base + json.dumps(items, separators=(",", ":"))).encode()).hexdigest()[:32])
    return keys


def frame_ranges(frames: list[int], gap: int = 0) -> list[tuple[int, int]]:
    """Sorted frames → inclusive (first, last) ranges, merging runs separated by ≤ ``gap`` frames
    (drawing a few extra frames is cheaper than starting another pass)."""
    out: list[list[int]] = []
    for f in sorted(set(frames)):
        if out and f - out[-1][1] <= gap + 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return [(a, b) for a, b in out]


def png_complete(path: Path) -> bool:
    """True when the file ends with the PNG end chunk (an interrupted write does not)."""
    try:
        size = path.stat().st_size
        if size < 64:
            return False
        with path.open("rb") as fh:
            fh.seek(size - len(PNG_END))
            return fh.read() == PNG_END
    except OSError:
        return False


def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


class FrameCache:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        return self.root / f"{key}.png"

    def has(self, key: str) -> bool:
        return self.path(key).is_file()

    def put(self, key: str, frame_file: Path, *, move: bool = False) -> None:
        dst = self.path(key)
        if dst.is_file():
            return
        if move:
            try:
                frame_file.replace(dst)          # same volume: a rename, no copy
                return
            except OSError:
                pass
        tmp = dst.with_suffix(".tmp")
        shutil.copyfile(frame_file, tmp)
        tmp.replace(dst)

    def adopt(self, frame_file: Path, key: str) -> bool:
        """Add a drawn frame to the cache (a hard link: the frame stays where it is)."""
        dst = self.path(key)
        if dst.is_file():
            return False
        try:
            os.link(frame_file, dst)
        except FileExistsError:
            return False
        except OSError:
            self.put(key, frame_file)
        return True

    def place(self, key: str, dst: Path) -> None:
        """Put a cached frame at ``dst`` (a hard link, or a copy across volumes)."""
        _link_or_copy(self.path(key), dst)

    def touch(self, keys) -> None:
        """Mark frames as recently used (pruning removes the least recently used first)."""
        for k in set(keys):
            try:
                os.utime(self.path(k))
            except OSError:
                pass

    def prune(self, max_bytes: int, keep: set[str] = frozenset()) -> int:
        """Delete least recently used frames until the cache fits ``max_bytes``."""
        files = []
        total = 0
        for p in self.root.glob("*.png"):
            try:
                st = p.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        removed = 0
        for _, size, p in sorted(files):
            if total <= max_bytes:
                break
            if p.stem in keep:
                continue
            try:
                p.unlink()
                total -= size
                removed += 1
            except OSError:
                pass
        return removed


def max_cache_bytes() -> int:
    try:
        gb = float(os.environ.get("CUTROOM_OVERLAY_CACHE_GB", DEFAULT_MAX_GB))
    except ValueError:
        gb = DEFAULT_MAX_GB
    return int(gb * 1024 ** 3)


def render_cached(props: dict, out_dir: Path, cache_dir: Path, scratch: Path, *, code_key: str,
                  concurrency: int | None = None, on_progress=None, gap: int = 4) -> dict:
    """Lay out every overlay frame of ``props`` in ``out_dir`` (NNNNNN.png), drawing with Remotion
    only the frames whose key is not cached yet, each distinct one once. Frames left in
    ``out_dir`` by an interrupted attempt are kept when complete. Returns counts."""
    from cutroom.graphics.remotion import normalize_alpha, render_overlay

    keys = frame_keys(props, code_key)
    scratch.mkdir(parents=True, exist_ok=True)
    cache = FrameCache(cache_dir)

    def frame_no(p: Path) -> int | None:
        try:
            f = int(p.stem)
        except ValueError:
            return None
        return f if 0 <= f < len(keys) else None

    if out_dir.is_dir():                                  # frames finished by an interrupted attempt
        for p in out_dir.glob("*.png"):
            if not png_complete(p):
                p.unlink()
        normalize_alpha(out_dir)
        for p in out_dir.glob("*.png"):
            f = frame_no(p)
            if f is not None:
                cache.adopt(p, keys[f])
        shutil.rmtree(out_dir)
    need: dict[str, int] = {}
    for f, k in enumerate(keys):
        if k not in need and not cache.has(k):
            need[k] = f
    drawn = 0
    present: set[int] = set()
    if need:
        # A new pass costs about as much as drawing a few frames: draw small gaps through.
        ranges = frame_ranges(list(need.values()), gap=gap)
        drawn = sum(b - a + 1 for a, b in ranges)
        render_overlay(props, out_dir, composition="Overlay", concurrency=concurrency, on_progress=on_progress,
                       scratch=scratch, ranges=ranges)
        for p in out_dir.glob("*.png"):                   # Remotion finished: every file is complete
            f = frame_no(p)
            if f is not None:
                cache.adopt(p, keys[f])
                present.add(f)
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        if on_progress:
            on_progress(1.0)
    first = next(iter(out_dir.glob("*.png")), None)
    width = len(first.stem) if first else max(6, len(str(len(keys) - 1)))
    for f, k in enumerate(keys):
        if f not in present:
            cache.place(k, out_dir / f"{f:0{width}d}.png")
    cache.touch(keys)
    cache.prune(max_cache_bytes(), keep=set(keys))
    return {"frames": len(keys), "distinct": len(set(keys)), "drawn": drawn, "reused": len(keys) - len(present)}
