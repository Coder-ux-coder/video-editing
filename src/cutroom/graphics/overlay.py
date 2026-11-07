"""Props for the Remotion "Overlay" composition: every graphic and the burned-in captions,
drawn in one pass as transparent frames that FFmpeg composites over the video."""

from __future__ import annotations

import base64
import math
import mimetypes
from pathlib import Path

from cutroom.errors import RefusedError
from cutroom.timeline.model import Timeline

ASSET_TYPES = {".png", ".jpg", ".jpeg", ".svg", ".webp"}


def asset_data_url(path: Path) -> str:
    if path.suffix.lower() not in ASSET_TYPES or not path.is_file():
        raise RefusedError(f"Graphic asset {path} is not a readable image.", fix=f"Use one of {sorted(ASSET_TYPES)}.")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def graphic_frames(tl: Timeline, fps: float, t0: float = 0.0, t1: float | None = None) -> list[tuple]:
    """(graphic, from_frame, to_frame) for graphics visible in [t0, t1), in frames relative to t0.
    A graphic cut by a range render keeps its full timing (it starts part-way through its
    animation; the frame before t0 can be negative)."""
    t1 = tl.duration if t1 is None else t1
    out = []
    for g in tl.graphics:
        if g.end <= t0 or g.start >= t1:
            continue
        f0, f1 = int(round((g.start - t0) * fps)), int(round((g.end - t0) * fps))
        if f1 > f0:
            out.append((g, f0, f1))
    return out


def graphics_props(tl: Timeline, *, fps: float, job_root: Path | None = None, t0: float = 0.0,
                   t1: float | None = None, theme: dict | None = None) -> list[dict]:
    from cutroom.graphics.contrast import needs_backing

    out = []
    total = int(round(tl.duration * fps))
    for g, f0, f1 in graphic_frames(tl, fps, t0, t1):
        params = dict(g.params)
        if g.template == "progress-bar":
            params["total"] = total
            params["chapters"] = [int(round(m.t * fps)) for m in tl.markers if m.kind == "chapter" and m.t > 0]
        for k, v in list(params.items()):
            if k == "src" and isinstance(v, str) and not v.startswith("data:") and job_root is not None:
                params[k] = asset_data_url(job_root / v)
        out.append({"id": g.id, "component": _component(g.template), "variant": g.variant, "from": f0,
                    "to": f1, "params": params, "placement": g.placement, "z": g.z,
                    "hides_captions": g.hides_captions,
                    "backing": g.backing if g.backing is not None else (
                        g.variant in _spec(g.template).backing_variants and needs_backing(theme or {}, g.bg_luminance))})
    return out


_SPECS: dict = {}


def _spec(template: str):
    if not _SPECS:
        from cutroom.graphics.templates import load_templates

        _SPECS.update(load_templates())
    return _SPECS[template]


def _component(template: str) -> str:
    return _spec(template).component


def hide_captions_under(cues: list[dict], graphics: list[dict]) -> tuple[list[dict], int]:
    """Drop or trim caption cues that overlap a graphic that hides captions (title cards,
    kinetic text: the words are already on screen). Returns (cues, number of cues affected)."""
    blocks = sorted((g["from"], g["to"]) for g in graphics if g.get("hides_captions"))
    if not blocks:
        return cues, 0
    out, touched = [], 0
    for c in cues:
        a, b = c["from"], c["to"]
        for g0, g1 in blocks:
            if g1 <= a or g0 >= b:
                continue
            touched += 1
            if g0 <= a and g1 >= b:
                a = b                       # fully covered: hidden
            elif g0 > a:
                b = min(b, g0)              # graphic starts mid-caption: end the caption there
            else:
                a = max(a, g1)              # graphic ends mid-caption: caption appears after it
        if b - a >= 6:                      # shorter than ~0.2 s would only flash
            out.append({**c, "from": a, "to": b, **({"trimmed": True} if a != c["from"] else {})})
    return out, touched


def overlay_props(tl: Timeline, style_doc: dict, *, width: int, height: int, fps: float, total_frames: int,
                  job_root: Path | None, captions: bool = True, t0: float = 0.0, t1: float | None = None) -> dict:
    """Everything the Overlay composition needs. ``t0``/``t1`` select a range render."""
    from cutroom.captions.plan import to_remotion_props

    theme = style_doc.get("graphics", {}).get("theme", {})
    graphics = graphics_props(tl, fps=fps, job_root=job_root, t0=t0, t1=t1, theme=theme)
    cap = None
    hidden = 0
    if captions and tl.captions and tl.captions.cues and tl.captions.burn_in:
        cp = to_remotion_props(tl.captions, style_doc, width=width, height=height, fps=fps,
                               duration=total_frames / fps)
        cues, hidden = hide_captions_under(cp["cues"], graphics)
        cap = {"cues": cues, "style": cp["style"], "safe": cp["safe"]}
    return {"width": width, "height": height, "fps": fps, "durationInFrames": max(1, total_frames),
            "theme": theme, "graphics": graphics, "captions": cap,
            "captions_hidden_by_graphics": hidden}


def caption_check_props(props: dict) -> dict | None:
    """Caption-only view of overlay props for the caption checks: frames where a graphic is drawn
    are excluded (their pixels would be counted as caption pixels)."""
    if not props.get("captions"):
        return None
    busy = [(g["from"], g["to"]) for g in props["graphics"] if g["component"] != "ProgressBar"]
    # A progress bar is a thin strip at the frame edge for the whole video: mask it instead of
    # skipping every frame.
    strips = sorted({"top" if g.get("variant") == "top" else "bottom"
                     for g in props["graphics"] if g["component"] == "ProgressBar"})
    return {"width": props["width"], "height": props["height"], "fps": props["fps"],
            "durationInFrames": props["durationInFrames"], "cues": props["captions"]["cues"],
            "safe": props["captions"]["safe"], "style": props["captions"]["style"], "busy_frames": busy,
            "mask_strips": strips}


def frames_total(duration: float, fps: float) -> int:
    return int(math.ceil(duration * fps))
