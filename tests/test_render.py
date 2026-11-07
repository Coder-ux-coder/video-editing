"""Render accuracy: exact frames, audio/video sync, no clicks at joins (and clicks detected without fades)."""

import subprocess

import numpy as np
import pytest
import soundfile as sf

from cutroom.ingest.ingest import ingest
from cutroom.ingest.proxy import extract_audio, make_proxy
from cutroom.job import Job
from cutroom.render.engine import detect_clicks, plan_segments, render, render_audio
from cutroom.timeline import store
from cutroom.timeline.ops import Removal, assemble, remove_ranges

from conftest import needs_ffmpeg

pytestmark = needs_ffmpeg
FPS = 25


def _luma_coded_source(path):
    """10 s, 25 fps: frame n has luma 16 + 8*(n mod 25); audio: 200 ms 1013 Hz burst at every whole second
    (1013 Hz so frame-aligned cuts land at varying phases: a hard join mid-tone then really clicks)."""
    sr = 48000
    t = np.arange(10 * sr) / sr
    audio = 0.005 * np.random.default_rng(0).standard_normal(len(t)).astype(np.float32)  # room tone, -46 dBFS
    for k in range(10):
        a = k * sr
        audio[a:a + int(0.2 * sr)] += 0.5 * np.sin(2 * np.pi * 1013 * t[: int(0.2 * sr)])
    wav = path.with_suffix(".wav")
    sf.write(str(wav), audio, sr)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=black:s=160x90:r={FPS}:d=10,format=gray,geq=lum='16+8*mod(N,25)'",
                    "-i", str(wav), "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)], check=True)
    wav.unlink()


def _frame_lumas(path) -> np.ndarray:
    """Mean of the raw Y plane per frame, as full-range luma. Uses a centre crop, never a scale:
    FFmpeg's scaler changes levels of colour-tagged HD frames, which would fake a failure."""
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vf", "crop=32:32",
                          "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 32 * 32 * 3 // 2)[:, : 32 * 32]
    return (frames.mean(axis=1) - 16) * 255 / 219


def _burst_times(path) -> list[float]:
    x = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", "48000",
                        "-f", "f32le", "-"], capture_output=True, check=True).stdout
    a = np.frombuffer(x, np.float32)
    env = np.convolve(np.abs(a), np.ones(240) / 240, mode="same")
    hot = env > 0.15
    starts = np.nonzero(np.diff(hot.astype(int)) == 1)[0]
    return [round(s / 48000, 3) for s in starts]


@pytest.fixture
def coded_job(jobs_base, tmp_path):
    src = tmp_path / "coded.mp4"
    _luma_coded_source(src)
    job = Job.create("render-test")
    ingest(job, [src])
    s = job.manifest.sources[0]
    make_proxy(job, s)
    extract_audio(job, s)
    return job


def test_frames_and_sync_are_exact(coded_job):
    job = coded_job
    tl = assemble(job)
    # Keep [0,2) [3,5) [7,10): remove 2-3 s and 5-7 s.
    remove_ranges(tl, [Removal("S01", 2.0, 3.0, "manual", "test"), Removal("S01", 5.0, 7.0, "manual", "test")],
                  tool="test", version=2, min_clip=0.1)
    n = store.save_new(job, tl, command="test", summary="test cuts")
    tl = store.load(job, n)
    for mode in ("preview", "final"):
        rep = render(job, tl, mode=mode, timecode=False)
        assert all(rep["checks"].values()), rep
        out = job.root / rep["path"]
        lum = _frame_lumas(out)
        src_frames = list(range(0, 50)) + list(range(75, 125)) + list(range(175, 250))
        expected = [n % 25 for n in src_frames]
        decoded = [int(round((v - 16) / 8)) for v in lum]   # nearest brightness level = frame number mod 25
        assert len(decoded) == len(expected) == 175
        assert decoded == expected, f"{mode}: wrong frames selected"
        # Bursts at source seconds 0,1,3,4,7,8,9 → output seconds 0..6.
        bursts = _burst_times(out)
        assert len(bursts) == 7
        # 12 ms: tight enough to catch AAC priming (21 ms) played as a delay. The very first
        # 21 ms of program audio are replaced by the encoder's priming by design (in real edits
        # that is head-pad silence), so the burst at t=0 is only checked loosely.
        assert np.abs(np.array(bursts[1:]) - np.arange(1, 7)).max() < 0.012, f"{mode}: audio out of sync: {bursts}"
        assert bursts[0] < 0.03
        assert rep["checks"]["no_edit_lists"]


def test_click_detector_finds_hard_joins_and_fades_remove_them(coded_job, tmp_path):
    job = coded_job
    tl = assemble(job)
    # Cut in the middle of tone bursts: a hard join there is an audible click.
    remove_ranges(tl, [Removal("S01", 1.08, 3.12, "manual", "test")], tool="test", version=2, min_clip=0.1)
    segs = plan_segments(tl)
    hard, soft = tmp_path / "hard.wav", tmp_path / "soft.wav"
    joins = render_audio(job, tl, segs, hard, join_fade=0.0)
    assert detect_clicks(hard, joins), "a hard cut through a tone should be detected as a click"
    joins = render_audio(job, tl, segs, soft, join_fade=0.008)
    assert not detect_clicks(soft, joins), "8 ms fades should remove the click"


def test_timeline_time_equals_rendered_time_after_many_cuts():
    """Clip lengths are frame-rounded at render time; the timeline must lay clips out the same
    way, or captions/graphics drift against the picture (found: 142 ms after 98 clips)."""
    from test_timeline import make_tl

    from cutroom.render.engine import plan_segments

    tl = make_tl(300.0)
    tl.meta.fps = 29.97
    rs = [Removal("S01", 1.013 + 2.7 * k, 1.013 + 2.7 * k + 0.517, "silence", "x") for k in range(100)]
    remove_ranges(tl, rs, tool="t", version=2, min_clip=0.05)
    segs = {s.clip_id: s.out_frame / tl.meta.fps for s in plan_segments(tl)}
    assert max(abs(c.start - segs[c.id]) for c in tl.track_clips()) < 1e-6
    assert abs(tl.duration - sum(s.frames for s in plan_segments(tl)) / tl.meta.fps) < 1e-6
