"""Where a graphic goes: inside the graphics safe area, above the caption band, off the face,
and not on top of another graphic. All boxes are fractions of the frame (x0, y0, x1, y1).

The estimate is checked after rendering on the drawn pixels (render report `graphics`),
because the real size of text depends on the font.
"""

from __future__ import annotations

from typing import Callable

from cutroom.graphics.templates import TemplateSpec

Box = tuple[float, float, float, float]
FREE_ZONES = {"full", "region", "edge"}   # drawn full-frame; no anchor to choose
BAND_CENTRES = (0.74, 0.26, 0.5)          # kinetic text: low (captions are hidden meanwhile), high, middle
BAND_HEIGHT = 0.16


def head_box(f: dict) -> Box:
    """A detected face grown to the whole head (hair above, chin below, ears to the sides)."""
    return (f["x"] - 0.15 * f["w"], f["y"] - 0.25 * f["h"], f["x"] + 1.15 * f["w"], f["y"] + 1.2 * f["h"])


def _area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def overlap(a: Box, b: Box) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def face_cover(box: Box, faces: list[dict]) -> float:
    """Largest share of any head covered by ``box`` (0 = clear, 1 = fully covered)."""
    worst = 0.0
    for f in faces:
        h = head_box(f)
        if _area(h) > 0:
            worst = max(worst, overlap(box, h) / _area(h))
    return round(worst, 3)


def caption_band(style_doc: dict, caption_style: str | None, width: int, height: int,
                 short: bool = False) -> tuple[float, float]:
    """Vertical band (y0, y1) reserved for two lines of bottom captions, whether or not captions
    exist yet (they may be planned after the graphics)."""
    from cutroom.captions.plan import resolved_style

    cfg = style_doc["captions"]
    style = cfg["styles"].get(caption_style or "clean") or next(iter(cfg["styles"].values()))
    rs = resolved_style(style, width, height)
    safe = cfg["safe_zones"]["youtube_short" if short else "youtube_long"]
    lines = min(style.get("lines", 2), cfg["max_lines"])
    pad = 2 * rs.get("box", {}).get("padY", 0) / height
    block = lines * rs["fontSize"] * rs["lineHeight"] / height + 0.02 + pad
    return (round(safe["bottom"] - block, 4), safe["bottom"])


def candidate_box(anchor: str, w: float, h: float, safe: dict, band: tuple[float, float] | None,
                  clearance: float) -> Box:
    v, hz = anchor.split("-")
    if hz == "left":
        x0 = safe["left"]
    elif hz == "right":
        x0 = safe["right"] - w
    else:
        x0 = 0.5 - w / 2
    floor = min(safe["bottom"], band[0] - clearance) if band else safe["bottom"]
    if v == "bottom":
        y0 = floor - h
    elif v == "top":
        y0 = safe["top"]
    else:   # centred in the space above the caption band
        y0 = (safe["top"] + floor) / 2 - h / 2
    return (round(x0, 4), round(y0, 4), round(x0 + w, 4), round(y0 + h, 4))


def placement_from_box(anchor: str, box: Box) -> dict:
    """Placement props for theme.js placeStyle: x is the edge on the anchor side, y the anchor edge
    (bottom edge for bottom-*, top edge for top-*, centre for center-*)."""
    v, hz = anchor.split("-")
    x = box[0] if hz == "left" else (box[2] if hz == "right" else 0.5)
    y = box[3] if v == "bottom" else (box[1] if v == "top" else (box[1] + box[3]) / 2)
    return {"anchor": anchor, "x": round(x, 4), "y": round(y, 4), "box": list(box)}


def place(spec: TemplateSpec, t0: float, t1: float, *, faces: Callable[[float, float], list[dict]] | None,
          safe: dict, band: tuple[float, float] | None, others: list[Box], clearance: float = 0.02,
          anchor: str | None = None, face_map: Callable[[float, dict], dict | None] | None = None) -> dict:
    """Choose the anchor whose box covers the least face, then the least of other graphics.
    ``face_map(t, face)`` maps a source-frame face box into the output frame (vertical crops)."""
    seen = faces(t0, t1) if faces else []
    if face_map is not None:
        seen = [m for m in (face_map((t0 + t1) / 2, f) for f in seen) if m]
    if spec.zone == "band":
        best = None
        portrait = safe.get("bottom", 0.93) < 0.8      # Shorts safe area: never in the platform's UI zone
        bh = 0.12 if portrait else BAND_HEIGHT
        if portrait:   # right under the top UI, or over the chest just above the captions
            chest = (band[0] if band else safe["bottom"]) - clearance - bh / 2
            centres = (safe["top"] + bh / 2 + 0.01, chest)
        else:
            centres = BAND_CENTRES
        for i, yc in enumerate(centres):
            box = (safe.get("left", 0.1) if portrait else 0.1, round(yc - bh / 2, 4),
                   safe.get("right", 0.9) if portrait else 0.9, round(yc + bh / 2, 4))
            cover = face_cover(box, seen)
            if best is None or (cover, i) < best[0]:
                best = ((cover, i), yc, box, cover)
        (cv, idx), yc, box, cover = best
        out = {"anchor": "band", "x": 0.5, "y": yc, "box": list(box), "face_cover": cover, "faces_sampled": len(seen)}
        if portrait and idx == 0:
            out.update(valign="top", y=box[1])     # under the top UI: grow downwards, never up into it
        return out
    if spec.zone in FREE_ZONES or not spec.anchors:
        return {"anchor": "center-center", "x": 0.5, "y": 0.5, "face_cover": 0.0}
    w, h = spec.size_hint["w"], spec.size_hint["h"]
    options = [anchor] if anchor and anchor != "auto" else spec.anchors
    best = None
    for i, a in enumerate(options):
        box = candidate_box(a, w, h, safe, band if spec.zone != "corner" else None, clearance)
        cover = face_cover(box, seen)
        crowd = sum(overlap(box, o) for o in others) / max(_area(box), 1e-9)
        cost = (cover * 10 + crowd * 5, i)
        if best is None or cost < best[0]:
            best = (cost, a, box, cover, crowd)
    _, a, box, cover, crowd = best
    return {**placement_from_box(a, box), "face_cover": cover, "overlaps_graphics": round(crowd, 3),
            "faces_sampled": len(seen)}
