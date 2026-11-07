"""Visual perception: shots, sampled frames, contact sheets, faces, framing, exposure, blur.

The agent cannot watch video. It looks at labelled contact sheets (images it can open)
and reads numeric reports computed from frames sampled on the proxy. Everything here runs
on the 540p proxy, so it is fast and the numbers are comparable across sources.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cutroom.errors import CutroomError
from cutroom.system.ffmpeg import require_tool, run_ffmpeg

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
import cv2  # noqa: E402

try:  # OpenCV 5 logs a harmless backend warning when YuNet loads.
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except AttributeError:  # pragma: no cover
    pass


# --------------------------------------------------------------------------- shots


def detect_shots(video: Path, duration: float, threshold: float = 10.0) -> list[dict]:
    """Shot boundaries with FFmpeg's scene-change detector. Returns [{start, end, score}]."""
    log = run_ffmpeg(["-i", str(video), "-an", "-vf", f"scdet=threshold={threshold}:sc_pass=0", "-f", "null", "-"],
                     desc="shot detection", duration=duration)
    cuts = sorted({round(float(t), 3) for t in re.findall(r"lavfi\.scd\.time:\s*([\d.]+)", log)})
    scores = dict(re.findall(r"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*([\d.]+)", log))
    edges = [0.0] + [c for c in cuts if 0.2 < c < duration - 0.2] + [duration]
    shots = []
    for a, b in zip(edges, edges[1:]):
        if b - a > 0.04:
            shots.append({"start": round(a, 3), "end": round(b, 3), "duration": round(b - a, 3)})
    _ = scores
    return shots


# --------------------------------------------------------------------------- frames


def grab_frames(video: Path, times: list[float], height: int = 270) -> list[np.ndarray]:
    """BGR frames at the given times (fast: the proxy has a keyframe every second)."""
    ffmpeg = require_tool("ffmpeg")
    frames = []
    for t in times:
        r = subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}",
                            "-i", str(video), "-frames:v", "1", "-vf", f"scale=-2:{height}", "-f", "image2pipe",
                            "-vcodec", "png", "-"], capture_output=True)
        img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR) if r.stdout else None
        frames.append(img)
    return frames


def _label(img: np.ndarray, text: str) -> np.ndarray:
    img = img.copy()
    scale = max(0.45, img.shape[0] / 500)
    (w, h), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    cv2.rectangle(img, (0, 0), (w + 10, h + base + 8), (0, 0, 0), -1)
    cv2.putText(img, text, (5, h + 4), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def contact_sheet(frames: list[np.ndarray | None], labels: list[str], out: Path, cols: int = 4,
                  title: str | None = None) -> Path:
    """Tile labelled frames into one JPEG the agent can open and look at."""
    good = [f for f in frames if f is not None]
    if not good:
        raise CutroomError("No frames could be read for the contact sheet.", fix="Check the proxy exists.")
    h, w = good[0].shape[:2]
    tiles = [_label(cv2.resize(f, (w, h)) if f is not None else np.zeros((h, w, 3), np.uint8), lab)
             for f, lab in zip(frames, labels)]
    rows = (len(tiles) + cols - 1) // cols
    tiles += [np.zeros((h, w, 3), np.uint8)] * (rows * cols - len(tiles))
    grid = np.vstack([np.hstack(tiles[r * cols:(r + 1) * cols]) for r in range(rows)])
    if title:
        bar = np.zeros((32, grid.shape[1], 3), np.uint8)
        cv2.putText(bar, title, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        grid = np.vstack([bar, grid])
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), grid, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return out


# --------------------------------------------------------------------------- faces and image quality


class FaceDetector:
    def __init__(self, score: float = 0.6):
        from cutroom.perception.models import ensure_file_model

        self.model = ensure_file_model("yunet")
        self.det = cv2.FaceDetectorYN.create(str(self.model), "", (320, 320), score, 0.3, 50)

    def detect(self, img: np.ndarray) -> list[dict]:
        h, w = img.shape[:2]
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(img)
        out = []
        for f in faces if faces is not None else []:
            x, y, fw, fh = (float(v) for v in f[:4])
            out.append({"x": round(x / w, 4), "y": round(y / h, 4), "w": round(fw / w, 4), "h": round(fh / h, 4),
                        "score": round(float(f[-1]), 3),
                        "mouth": [round(float(f[10]) / w, 4), round(float(f[11]) / h, 4),
                                  round(float(f[12]) / w, 4), round(float(f[13]) / h, 4)]})
        return sorted(out, key=lambda d: -d["w"] * d["h"])


def image_quality(img: np.ndarray) -> dict:
    """Exposure, clipping, sharpness and colour cast of one frame."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    return {"luma": round(float(gray.mean()), 1),
            "clipped_highlights": round(float((gray >= 250).mean()), 4),
            "crushed_shadows": round(float((gray <= 5).mean()), 4),
            "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1),
            "cast_a": round(float(lab[..., 1].mean() - 128), 1), "cast_b": round(float(lab[..., 2].mean() - 128), 1)}


def face_quality(img: np.ndarray, face: dict) -> dict:
    """Exposure of the subject's face: what matters, unlike a bright sky or white sand behind them."""
    h, w = img.shape[:2]
    x0, y0 = max(0, int(face["x"] * w)), max(0, int(face["y"] * h))
    x1, y1 = min(w, int((face["x"] + face["w"]) * w)), min(h, int((face["y"] + face["h"]) * h))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return {}
    g = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    return {"face_luma": round(float(g.mean()), 1), "face_clipped": round(float((g >= 250).mean()), 4),
            "face_crushed": round(float((g <= 8).mean()), 4)}


@dataclass
class FrameSample:
    t: float
    faces: list[dict]
    quality: dict


def framing(face: dict) -> dict:
    """Composition metrics for the main face (all relative to frame size)."""
    cx = face["x"] + face["w"] / 2
    return {"face_center_x": round(cx, 3), "face_top": round(face["y"], 3),
            "face_height": round(face["h"], 3), "off_center": round(abs(cx - 0.5), 3)}


def sample_video(video: Path, duration: float, every: float = 1.0, max_samples: int = 900) -> list[FrameSample]:
    step = max(every, duration / max_samples)
    times = [round(t, 3) for t in np.arange(step / 2, duration, step)]
    det = FaceDetector()
    samples = []
    # One FFmpeg pass that emits the sampled frames as raw images is much faster than seeking.
    ffmpeg = require_tool("ffmpeg")
    h = 270
    probe_frame = grab_frames(video, [times[0]], h)[0] if times else None
    if probe_frame is None:
        return samples
    w = probe_frame.shape[1]
    proc = subprocess.Popen([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(video), "-vf",
                             f"fps=1/{step:.6f}:start_time={step / 2:.3f},scale={w}:{h}",
                             "-frames:v", str(len(times)), "-f", "rawvideo",
                             "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    size = w * h * 3
    assert proc.stdout is not None
    for t in times:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        img = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        faces = det.detect(img)
        q = image_quality(img)
        if faces:
            q.update(face_quality(img, faces[0]))
        samples.append(FrameSample(t, faces, q))
    proc.stdout.close()
    proc.wait()
    return samples


def summarize_shot(shot: dict, samples: list[FrameSample]) -> dict:
    inside = [s for s in samples if shot["start"] <= s.t < shot["end"]]
    if not inside:
        return {**shot, "samples": 0}
    with_face = [s for s in inside if s.faces]
    multi = [s for s in inside if len(s.faces) >= 2]
    keys = sorted({k for s in inside for k in s.quality})
    q = {k: round(float(np.median([s.quality[k] for s in inside if k in s.quality])), 3) for k in keys}
    out = {**shot, "samples": len(inside), "face_presence": round(len(with_face) / len(inside), 2),
           "multi_face": round(len(multi) / len(inside), 2), "quality": q, "flags": []}
    if with_face:
        fr = [framing(s.faces[0]) for s in with_face]
        out["framing"] = {k: round(float(np.median([f[k] for f in fr])), 3) for k in fr[0]}
        f = out["framing"]
        if f["face_top"] < 0.03:
            out["flags"].append("face touches the top edge (cut-off head / no headroom)")
        if f["off_center"] > 0.3:
            out["flags"].append("face far off-centre")
        if f["face_height"] < 0.12:
            out["flags"].append("face is small in frame (wide shot)")
    # Exposure is judged on the face when there is one: a bright sky or white sand behind a
    # well-lit face is a look, not a fault.
    if "face_luma" in q and out["face_presence"] >= 0.5:
        if q["face_luma"] < 45 or q["face_crushed"] > 0.2:
            out["flags"].append(f"face underexposed (face luma {q['face_luma']})")
        if q["face_clipped"] > 0.05 or q["face_luma"] > 225:
            out["flags"].append(f"face overexposed ({q['face_clipped']:.0%} of face clipped)")
    else:
        if q["luma"] < 40 or q["crushed_shadows"] > 0.3:
            out["flags"].append("underexposed")
        if q["luma"] > 225 or q["clipped_highlights"] > 0.2:
            out["flags"].append("overexposed / clipped highlights")
    if q["sharpness"] < 30:
        out["flags"].append("soft or blurry")
    if abs(q["cast_a"]) > 12 or abs(q["cast_b"]) > 15:
        out["flags"].append(f"colour cast (a {q['cast_a']}, b {q['cast_b']})")
    return out


def classify_footage(speech_coverage: float, shots: list[dict], samples: list[FrameSample],
                     duration: float) -> dict:
    """Best guess at the footage type, with the evidence, for the agent to confirm."""
    n = max(1, len(samples))
    face = sum(1 for s in samples if s.faces) / n
    multi = sum(1 for s in samples if len(s.faces) >= 2) / n
    shots_per_min = len(shots) / max(duration / 60, 1e-6)
    sharp = float(np.median([s.quality["sharpness"] for s in samples])) if samples else 0.0
    evidence = {"speech_coverage": round(speech_coverage, 2), "face_presence": round(face, 2),
                "two_plus_faces": round(multi, 2), "shots_per_minute": round(shots_per_min, 2),
                "median_sharpness": round(sharp, 1)}
    if face < 0.2 and speech_coverage >= 0.3 and sharp > 200:
        kind, conf = "screen_recording", 0.6
    elif speech_coverage >= 0.5 and multi >= 0.3:
        kind, conf = "podcast", 0.65
    elif face >= 0.6 and speech_coverage >= 0.5 and shots_per_min < 3:
        kind, conf = "talking_head", 0.8
    elif shots_per_min >= 6 or (speech_coverage < 0.4 and face < 0.5):
        kind, conf = "vlog", 0.55
    else:
        kind, conf = "mixed", 0.5
    return {"type": kind, "confidence": conf, "evidence": evidence}
