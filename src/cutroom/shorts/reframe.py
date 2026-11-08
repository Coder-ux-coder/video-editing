"""Vertical reframing: a 9:16 window that follows the speaker's face, or a blurred-fill layout.

Per clip (a cut may jump the framing; inside a clip it must glide or stay still):
- Too few face samples, or two faces of similar size (a two-shot) → ``fill``: the whole 16:9
  frame fitted to the width over a blurred, darkened copy of itself.
- Otherwise ``crop``: a full-height window of width ih·9/16 centred on the face. If the face stays
  within a small spread the window does not move (most talking heads); otherwise it follows a
  smoothed track with a speed limit, so it never whips.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cutroom.timeline.model import Clip, Effect, Keyframe

FILL = "vertical_fill"


def crop_half_width(src_w: int, src_h: int, out_w: int, out_h: int) -> float:
    """Half the crop window's width as a fraction of the source width."""
    return (src_h * out_w / out_h) / src_w / 2


@dataclass
class ClipFraming:
    mode: str                     # "crop" or "fill"
    keyframes: list[tuple[float, float]]   # (clip-relative t, crop centre x) for crop
    reason: str


def plan_clip(samples: list[dict], src_in: float, src_out: float, half: float, cfg: dict) -> ClipFraming:
    """``samples``: faces.json entries [{t, faces:[{x,y,w,h}]}] (source time)."""
    inside = [s for s in samples if src_in - 0.5 <= s["t"] <= src_out + 0.5]
    if not inside:
        return ClipFraming("fill", [], "no face samples")
    with_face = [s for s in inside if s["faces"]]
    presence = len(with_face) / len(inside)
    if presence < cfg.get("face_presence_min", 0.6):
        return ClipFraming("fill", [], f"face in {presence:.0%} of samples")
    two = [s for s in with_face if len(s["faces"]) >= 2 and
           s["faces"][1]["w"] * s["faces"][1]["h"] >= 0.5 * s["faces"][0]["w"] * s["faces"][0]["h"]]
    if len(two) / len(with_face) > 0.3:
        return ClipFraming("fill", [], "two people in frame")
    ts = np.array([s["t"] for s in with_face])
    xs = np.array([s["faces"][0]["x"] + s["faces"][0]["w"] / 2 for s in with_face])
    lo, hi = half, 1 - half
    if np.std(xs) <= cfg.get("static_spread", 0.035) or len(xs) < 3:
        x = float(np.clip(np.median(xs), lo, hi))
        return ClipFraming("crop", [(0.0, round(x, 4))], "steady face: still crop")
    # Moving: smooth (median of 3, then moving average), speed-limit, keyframe every sample.
    med = np.array([np.median(xs[max(0, i - 1):i + 2]) for i in range(len(xs))])
    sm = np.convolve(np.pad(med, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    vmax = cfg.get("max_speed", 0.12)
    out = [float(np.clip(sm[0], lo, hi))]
    for i in range(1, len(sm)):
        dt = max(1e-3, ts[i] - ts[i - 1])
        step = float(np.clip(sm[i] - out[-1], -vmax * dt, vmax * dt))
        out.append(float(np.clip(out[-1] + step, lo, hi)))
    keys = [(round(max(0.0, t - src_in), 3), round(x, 4)) for t, x in zip(ts, out)]
    keys = [k for k in keys if k[0] <= src_out - src_in] or keys[:1]
    return ClipFraming("crop", keys, "moving face: smoothed, speed-limited track")


def plan_clip_shots(samples: list[dict], src_in: float, src_out: float, half: float, cfg: dict,
                    shots: list[float], frame: float = 1 / 30) -> ClipFraming:
    """Plan each source shot inside the clip separately and jump at the camera change (a
    smoothed pan across a hard cut would look like a mistake)."""
    cuts = [s for s in shots if src_in + 0.3 < s < src_out - 0.3]
    whole = plan_clip(samples, src_in, src_out, half, cfg)
    if not cuts or whole.mode == "fill":
        return whole
    keys: list[tuple[float, float]] = []
    edges = [src_in, *cuts, src_out]
    reasons = []
    for a, b in zip(edges, edges[1:]):
        part = plan_clip(samples, a, b, half, cfg)
        if part.mode == "fill":       # a shot without a usable face inside a cropped clip: hold the framing
            part = ClipFraming("crop", [(0.0, keys[-1][1] if keys else 0.5)], "no face in this shot: hold")
        off = a - src_in
        if keys:
            keys.append((round(off - frame, 3), keys[-1][1]))      # hold until the camera change …
        keys += [(round(off + t, 3), x) for t, x in part.keyframes]   # … then jump
        reasons.append(part.reason)
    return ClipFraming("crop", keys, "per shot: " + "; ".join(reasons))


def apply_framing(clip: Clip, f: ClipFraming) -> None:
    clip.effects = [e for e in clip.effects if e.name != FILL]
    if f.mode == "fill":
        clip.reframe = []
        clip.effects.append(Effect(name=FILL, params={"reason": f.reason}))
    else:
        clip.reframe = [Keyframe(t=t, x=x, y=0.5, scale=1.0, ease="linear") for t, x in f.keyframes]


def frame_vertical(clips: list[Clip], faces: dict[str, list[dict]], src_size: dict[str, tuple[int, int]],
                   out_w: int, out_h: int, cfg: dict, shots: dict[str, list[float]] | None = None) -> dict:
    """Plan and store the framing of every clip; returns counts for the summary."""
    counts = {"crop_still": 0, "crop_moving": 0, "fill": 0}
    for c in clips:
        w, h = src_size[c.source]
        f = plan_clip_shots(faces.get(c.source, []), c.src_in, c.src_out, crop_half_width(w, h, out_w, out_h), cfg,
                            (shots or {}).get(c.source, []))
        apply_framing(c, f)
        counts["fill" if f.mode == "fill" else ("crop_still" if len(f.keyframes) == 1 else "crop_moving")] += 1
    return counts


def output_face_mapper(tl):
    """``map(t, face)``: a face box in source-frame fractions → output-frame fractions at timeline
    time ``t`` (through the clip's crop window, or the fitted frame of the fill layout)."""
    clips = tl.track_clips()

    def to_out(t: float, f: dict) -> dict | None:
        c = next((c for c in clips if c.start <= t < c.end), None)
        if c is None:
            return f
        src = tl.source(c.source)
        if not (src.width and src.height and src.width > src.height and tl.meta.height > tl.meta.width):
            return f
        if any(e.name == FILL for e in c.effects):
            scale = (tl.meta.width / tl.meta.height) * src.height / src.width   # fitted to the width, centred
            return {**f, "y": 0.5 - scale / 2 + f["y"] * scale, "h": f["h"] * scale}
        half = crop_half_width(src.width, src.height, tl.meta.width, tl.meta.height)
        cx = c.reframe[0].x if c.reframe else 0.5
        if len(c.reframe) > 1:     # moving crop: centre at this moment
            rel = t - c.start
            ks = c.reframe
            for k0, k1 in zip(ks, ks[1:]):
                if k0.t <= rel <= k1.t:
                    cx = k0.x + (k1.x - k0.x) * (rel - k0.t) / max(1e-6, k1.t - k0.t)
                    break
            else:
                cx = ks[-1].x if rel > ks[-1].t else ks[0].x
        x0 = cx - half
        return {**f, "x": (f["x"] - x0) / (2 * half), "w": f["w"] / (2 * half)}
    return to_out
