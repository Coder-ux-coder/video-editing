"""YouTube thumbnail candidates: the best frames of the speaker, a short title on the side away
from the face, checked against YouTube's limits and for legibility.

Frames are scored on the face (size, sharpness, exposure) across the edit, then spread out (at
least 20 s and a different look apart). The title must be a few words the video supports: its
numbers are verified against the transcript like any on-screen fact.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import cv2
import numpy as np

from cutroom.errors import CutroomError
from cutroom.job import Job
from cutroom.timeline.model import Timeline

W, H = 1280, 720
MAX_BYTES = 2 * 1024 * 1024      # YouTube thumbnail limit (2 MB)


def candidate_moments(job: Job, tl: Timeline, n: int = 3, min_gap: float = 20.0) -> list[dict]:
    """Timeline moments with a large, sharp, well-exposed face, spread over the video."""
    from cutroom.perception import visual as V

    scored = []
    for c in tl.track_clips():
        p = job.root / "analysis" / c.source / "faces.json"
        if not p.is_file():
            continue
        for smp in json.loads(p.read_text(encoding="utf-8")):
            if not (c.src_in + 0.3 <= smp["t"] <= c.src_out - 0.3) or not smp["faces"]:
                continue
            f = smp["faces"][0]
            size = f["h"]
            centred = 1 - abs(f["x"] + f["w"] / 2 - 0.5)
            scored.append((size * 2 + centred * 0.3, c.to_timeline(smp["t"]), c.source, smp["t"], f))
    if not scored:
        raise CutroomError("No faces found in the edit for a thumbnail.", fix="Run `cutroom analyze` (faces), or choose moments with --at (seconds or m:ss).")
    scored.sort(key=lambda x: -x[0])
    pool = scored[: max(30, n * 10)]
    src = {s.id: s for s in tl.sources}
    out = []
    for score, t, sid, st, f in pool:
        if any(abs(t - o["t"]) < min_gap for o in out):
            continue
        rec = job.source(sid)
        img = V.grab_frames(job.source_path(rec), [st], 720)[0]
        if img is None:
            continue
        h, w = img.shape[:2]
        x0, y0 = int(f["x"] * w), int(f["y"] * h)
        x1, y1 = int((f["x"] + f["w"]) * w), int((f["y"] + f["h"]) * h)
        face = cv2.cvtColor(img[max(0, y0):y1, max(0, x0):x1], cv2.COLOR_BGR2GRAY)
        if face.size == 0:
            continue
        sharp = float(cv2.Laplacian(face, cv2.CV_64F).var())
        luma = float(face.mean())
        quality = score + min(sharp, 400) / 400 - (0.5 if luma < 60 or luma > 220 else 0)
        out.append({"t": round(t, 2), "source": sid, "src_t": st, "face": f, "sharpness": round(sharp, 1),
                    "face_luma": round(luma, 1), "score": round(quality, 3)})
        if len(out) >= n * 2:
            break
    out.sort(key=lambda d: -d["score"])
    del src
    return sorted(out[:n], key=lambda d: d["t"])


def moments_at(job: Job, tl: Timeline, times: list[float]) -> list[dict]:
    """Moments the agent chose (timeline seconds): for footage without a usable face (screen
    recordings, products, landscapes) or to override the face-based picks. A face detected near the
    moment is still kept clear of the title."""
    out = []
    for t in times:
        c = next((c for c in tl.track_clips() if c.start <= t < c.end), None)
        if c is None:
            raise CutroomError(f"{t:.2f}s is outside the edit ({tl.duration:.2f}s).", fix="Give a time inside the edit.")
        st = c.to_source(t)
        face = None
        p = job.root / "analysis" / c.source / "faces.json"
        if p.is_file():
            near = [smp for smp in json.loads(p.read_text(encoding="utf-8")) if smp["faces"] and abs(smp["t"] - st) <= 0.5]
            if near:
                face = min(near, key=lambda smp: abs(smp["t"] - st))["faces"][0]
        out.append({"t": round(t, 2), "source": c.source, "src_t": st, "face": face})
    return out


def compose(job: Job, tl: Timeline, moment: dict, text: str, emphasis: list[str], out_path: Path, theme: dict,
            scratch: Path, zoom: float = 0.8) -> dict:
    """Render one thumbnail and check it. The frame is recomposed as a tighter 16:9 crop that puts
    the face on the thirds line opposite the title (bigger face, clear text side)."""
    from cutroom.graphics.remotion import render_overlay
    from cutroom.perception import visual as V

    rec = job.source(moment["source"])
    frame = V.grab_frames(job.source_path(rec), [moment["src_t"]], 1080)[0]
    fh, fw = frame.shape[:2]
    f = moment.get("face")
    if f:
        fcx, fcy = (f["x"] + f["w"] / 2) * fw, (f["y"] + f["h"] / 2) * fh
        side = "left" if fcx >= fw / 2 else "right"       # text goes where the face is not
        ch = int(fh * zoom)
        cw = min(fw, int(ch * 16 / 9))
        target = 0.68 if side == "left" else 0.32
        x0 = int(min(max(0, fcx - target * cw), fw - cw))
        y0 = int(min(max(0, fcy - 0.42 * ch), fh - ch))
        face_box = ((f["x"] * fw - x0) / cw, (f["y"] * fh - y0) / ch, ((f["x"] + f["w"]) * fw - x0) / cw,
                    ((f["y"] + f["h"]) * fh - y0) / ch)
    else:                                                 # no face: the whole 16:9 frame, title left
        side = "left"
        cw = min(fw, int(fh * 16 / 9))
        ch = int(cw * 9 / 16)
        x0, y0 = (fw - cw) // 2, (fh - ch) // 2
        face_box = (0.0, 0.0, 0.0, 0.0)
    crop = cv2.resize(frame[y0:y0 + ch, x0:x0 + cw], (W, H), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
    base = {"image": "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode(), "emphasis": emphasis,
            "side": side, "theme": theme, "width": W, "height": H}
    imgs = []
    # the thumbnail, the same without the title (what is behind the glyphs), and the title alone
    # on black (where the glyphs are, whatever the background's colour)
    for k, (t, image) in enumerate(((text, base["image"]), ("", base["image"]), (text, None))):
        raw = scratch / f"thumb_{out_path.stem}_{k}"
        render_overlay({**base, "text": t, "image": image}, raw, composition="Thumbnail", frames=[0], scratch=scratch)
        imgs.append(cv2.imread(str(raw / "0.png")))
    img, bg, alone = imgs
    mask = cv2.cvtColor(alone, cv2.COLOR_BGR2GRAY) > 80
    q = 92
    while True:
        cv2.imwrite(str(out_path), img, [cv2.IMWRITE_JPEG_QUALITY, q])
        if out_path.stat().st_size <= MAX_BYTES or q <= 60:
            break
        q -= 8
    checks = check_thumbnail(out_path, img, bg, mask, face_box, theme, bool(emphasis))
    return {"path": str(out_path), "t": moment["t"], "side": side, "jpeg_quality": q, **checks}


def _rel_lum(bgr: np.ndarray) -> np.ndarray:
    c = bgr.astype(np.float32) / 255
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.0722 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.2126 * lin[..., 2]


def check_thumbnail(path: Path, img: np.ndarray, bg: np.ndarray, glyph: np.ndarray, face_box: tuple, theme: dict,
                    accent: bool) -> dict:
    """Size and weight within YouTube's limits; the title's colours against the brightest 10 % of
    the background behind its glyphs reach 3:1 (WCAG large text); no glyph on the face."""
    from cutroom.graphics.contrast import luminance, parse_color, ratio

    h, w = img.shape[:2]
    behind = float(np.percentile(_rel_lum(bg)[glyph], 90)) if glyph.any() else 0.0
    colors = theme.get("colors", {})
    text_l = [luminance(parse_color(colors.get("text", "#FFFFFF"))[:3])]
    if accent:
        text_l.append(luminance(parse_color(colors.get("accent", "#FFD60A"))[:3]))
    contrast = min(ratio(t, behind) for t in text_l)
    x0, y0, x1, y1 = (max(0, int(face_box[0] * w)), max(0, int(face_box[1] * h)), min(w, int(face_box[2] * w)),
                      min(h, int(face_box[3] * h)))
    on_face = float(glyph[y0:y1, x0:x1].mean()) if x1 > x0 and y1 > y0 else 0.0
    return {"checks": {"size_1280x720": bool((w, h) == (W, H)), "under_2mb": bool(path.stat().st_size <= MAX_BYTES),
                       "face_clear_of_text": bool(on_face <= 0.01), "text_contrast": bool(contrast >= 3.0)},
            "bytes": path.stat().st_size, "contrast": round(float(contrast), 1), "text_on_face": round(on_face, 3)}
