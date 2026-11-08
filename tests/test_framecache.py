"""Overlay frame cache: keys say exactly when two frames are identical, and cached renders are
pixel-identical to drawing every frame."""

import copy
import re
from pathlib import Path

import pytest

from cutroom.graphics.framecache import (GLOBAL_LENGTH_COMPONENTS, FrameCache, frame_keys, png_complete)

ROOT = Path(__file__).resolve().parents[1]


def _cue(a, words, step=6, cid="q"):
    lines = [[{"text": w, "from": a + i * step, "to": a + (i + 1) * step, "emph": False} for i, w in enumerate(words)]]
    return {"id": cid, "from": a, "to": a + len(words) * step, "placement": "bottom", "scrim": False, "lines": lines}


def _props(n=60, cues=None, graphics=None, w=320, h=180):
    return {"width": w, "height": h, "fps": 25, "durationInFrames": n, "theme": {"t": 1},
            "graphics": graphics or [],
            "captions": {"cues": cues or [], "style": {"fontFamily": "Inter"}, "safe": {}} if cues is not None else None}


def _shift(props, d, extra=0):
    """The same content d frames later (a cut or an insertion before it)."""
    p = copy.deepcopy(props)
    p["durationInFrames"] += d + extra
    for g in p["graphics"]:
        g["from"] += d
        g["to"] += d
    for c in (p["captions"] or {}).get("cues", []):
        c["from"] += d
        c["to"] += d
        for line in c["lines"]:
            for wd in line:
                wd["from"] += d
                wd["to"] += d
    return p


HEADLINE = {"id": "g1", "component": "KineticText", "variant": "stamp", "from": 5, "to": 30,
            "params": {"text": "Hello there", "size": "title"}, "placement": {"y": 0.3}, "z": 0, "backing": False}


def test_shifted_content_has_the_same_keys():
    p = _props(cues=[_cue(10, ["one", "two", "three"]), _cue(40, ["four"])], graphics=[dict(HEADLINE)])
    k1 = frame_keys(p, "code")
    k2 = frame_keys(_shift(p, 7), "code")
    assert k2[7:] == k1
    assert len(set(k2[:7])) == 1 and k2[0] == k1[0]          # blank frames: one key, shared


def test_any_change_on_screen_changes_the_key():
    base = _props(cues=[_cue(10, ["one", "two"])], graphics=[dict(HEADLINE)])
    k = frame_keys(base, "code")
    edited = copy.deepcopy(base)
    edited["captions"]["cues"][0]["lines"][0][1]["text"] = "TWO"
    ke = frame_keys(edited, "code")
    assert ke[:10] == k[:10] and ke[10] != k[10]             # the cue is on screen from frame 10
    moved = copy.deepcopy(base)
    moved["graphics"][0]["placement"] = {"y": 0.6}
    assert frame_keys(moved, "code")[5] != k[5]
    assert frame_keys(base, "other-code")[0] != k[0]
    assert frame_keys(_props(cues=[_cue(10, ["one", "two"])], graphics=[dict(HEADLINE)], w=640, h=360), "code")[0] != k[0]
    # A word whose timing within the cue changes is a different frame.
    retimed = copy.deepcopy(base)
    retimed["captions"]["cues"][0]["lines"][0][1]["from"] += 1
    assert frame_keys(retimed, "code")[16] != k[16]


def test_graphics_that_read_the_video_length_key_on_it():
    bar = {"id": "p", "component": "ProgressBar", "variant": "bottom", "from": 0, "to": 60, "params": {},
           "placement": None, "z": 0, "backing": False}
    a = frame_keys(_props(n=60, graphics=[dict(bar)]), "c")
    b = frame_keys(_props(n=61, graphics=[{**bar, "to": 60}]), "c")
    assert a[10] != b[10]


def test_global_length_components_match_the_sources():
    """Every component that reads useVideoConfig().durationInFrames must be in the set."""
    found = set()
    for f in (ROOT / "graphics" / "src" / "components" / "graphics").glob("*.jsx"):
        blocks = re.split(r"(?=^export const )", f.read_text(encoding="utf-8"), flags=re.M)
        for b in blocks:
            m = re.match(r"export const (\w+)", b)
            if m and re.search(r"\{[^}]*\bdurationInFrames\b[^}]*\}\s*=\s*useVideoConfig\(\)", b):
                found.add(m.group(1))
    assert found <= GLOBAL_LENGTH_COMPONENTS, f"add {sorted(found - GLOBAL_LENGTH_COMPONENTS)} to GLOBAL_LENGTH_COMPONENTS"
    overlay = (ROOT / "graphics" / "src" / "components" / "Overlay.jsx").read_text(encoding="utf-8")
    assert "<Sequence" in overlay and "from={g.from}" in overlay   # graphics see local frames only


def test_caption_frames_key_on_their_animation_state():
    p = _props(n=30, cues=[_cue(2, ["one", "two"], step=10), _cue(22, ["three"])])
    st = [None] * 30
    for f in range(2, 22):
        st[f] = [0, {"opacity": 1, "active": 0 if f < 12 else 1, "wordScale": 1 if (f - 2) % 10 > 6 else f}]
    for f in range(22, 28):
        st[f] = [1, {"opacity": 1, "active": 0, "wordScale": 1}]
    k = frame_keys(p, "c", st)
    assert k[9] == k[10] == k[11] and k[19] == k[20] != k[10]   # held still: one frame
    assert k[3] != k[4] and k[22] != k[10]                      # popping; another cue
    stale = [None if x is None else [x[0] + 1, x[1]] for x in st]
    assert frame_keys(p, "c", stale) == frame_keys(p, "c")      # not this cue: frame-relative keys
    assert frame_keys(p, "c", st[:5]) == frame_keys(p, "c")     # wrong length: ignored


def test_captions_draw_time_only_through_caption_state():
    """The cache keys caption frames on captionState: Captions.jsx must not read the frame otherwise."""
    src = (ROOT / "graphics" / "src" / "components" / "Captions.jsx").read_text(encoding="utf-8")
    uses = [ln for ln in src.splitlines() if re.search(r"\bframe\b", ln)
            and not ln.lstrip().startswith(("*", "//", "/*"))]
    allowed = ("useCurrentFrame()", "findCue(cues, frame)", "captionState(cue, frame, style, fps)")
    assert all(any(a in u for a in allowed) for u in uses), [u for u in uses if not any(a in u for a in allowed)]
    assert "spring(" not in src and "Date" not in src and "random" not in src.lower()


def test_cache_links_frames_and_prunes_least_recently_used(tmp_path):
    import os
    import time

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 80 + b"IEND\xaeB`\x82"
    frames = tmp_path / "frames"
    frames.mkdir()
    for i in range(3):
        (frames / f"{i:06d}.png").write_bytes(png + bytes([i]) * 0)
    (frames / "000003.png").write_bytes(png[:-4])            # an interrupted write
    assert png_complete(frames / "000000.png") and not png_complete(frames / "000003.png")
    cache = FrameCache(tmp_path / "cache")
    assert cache.adopt(frames / "000000.png", "a") and not cache.adopt(frames / "000001.png", "a")
    cache.adopt(frames / "000001.png", "b")
    out = tmp_path / "out"
    out.mkdir()
    cache.place("a", out / "000000.png")
    assert (out / "000000.png").read_bytes() == png
    old = time.time() - 1000
    os.utime(cache.path("a"), (old, old))
    assert cache.prune(max_bytes=len(png)) == 1 and not cache.has("a") and cache.has("b")
    assert cache.prune(max_bytes=0, keep={"b"}) == 0 and cache.has("b")


# ------------------------------------------------------------------ real frames (Remotion)


@pytest.mark.slow
def test_cached_renders_are_pixel_identical_to_drawing_every_frame(tmp_path):
    """First render, a shifted version and an edited version, each against a full fresh draw."""
    import cv2
    import numpy as np

    from cutroom.captions.plan import resolved_style
    from cutroom.graphics.framecache import render_cached
    from cutroom.graphics.remotion import render_overlay
    from cutroom.render.engine import _graphics_key
    from cutroom.style import load_style

    style = load_style()
    w, h = 320, 180
    cap_style = resolved_style(style["captions"]["styles"]["bold-karaoke"], w, h)
    base = _props(n=48, w=w, h=h, cues=[_cue(4, ["Cache", "every", "frame"]), _cue(30, ["once"], step=10)],
                  graphics=[{**HEADLINE, "from": 0, "to": 20}])
    base["theme"] = style["graphics"]["theme"]
    base["captions"].update(style=cap_style, safe=style["captions"]["safe_zones"]["youtube_long"])
    shifted = _shift(base, 9)
    edited = copy.deepcopy(shifted)
    edited["captions"]["cues"][1]["lines"][0][0]["text"] = "twice"
    cache_dir = tmp_path / "cache"
    code = _graphics_key()

    def same(a: Path, b: Path) -> list[int]:
        fa = sorted(a.glob("*.png"), key=lambda p: int(p.stem))
        fb = sorted(b.glob("*.png"), key=lambda p: int(p.stem))
        assert len(fa) == len(fb)
        return [i for i, (x, y) in enumerate(zip(fa, fb)) if not np.array_equal(
            cv2.imread(str(x), cv2.IMREAD_UNCHANGED), cv2.imread(str(y), cv2.IMREAD_UNCHANGED))]

    stats = []
    for name, props in (("base", base), ("shifted", shifted), ("edited", edited)):
        ref = tmp_path / f"{name}_ref"
        render_overlay(props, ref, composition="Overlay", concurrency=2, scratch=tmp_path)
        out = tmp_path / f"{name}_cached"
        stats.append(render_cached(props, out, cache_dir, tmp_path / f"{name}_scratch", code_key=code, concurrency=2))
        assert same(ref, out) == [], name
    assert stats[0]["distinct"] < stats[0]["frames"] - 8          # held caption frames drawn once
    assert stats[1]["drawn"] == 0                                 # only moved: nothing to draw
    assert 0 < stats[2]["drawn"] < stats[2]["frames"] // 2        # one word changed: only its cue
