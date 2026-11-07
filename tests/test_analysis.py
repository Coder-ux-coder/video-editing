"""Audio report, visual report, contact sheets, frames sampler, topic task, pacing."""

import json

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

from cutroom.cli import app
from cutroom.ingest.proxy import extract_audio, make_proxy
from cutroom.perception import pipeline as P
from cutroom.perception import topics as T
from cutroom.perception.audio import clipping, envelope_db, levels, measure_loudness, silences
from cutroom.perception.visual import classify_footage, contact_sheet, face_quality, image_quality

from conftest import needs_ffmpeg
from test_cutting import seq

runner = CliRunner()


def test_envelope_levels_and_silences():
    sr = 48000
    x = np.concatenate([np.zeros(sr), 0.3 * np.sin(np.arange(sr) * 2 * np.pi * 200 / sr), np.zeros(sr)]).astype(np.float32)
    x += 1e-4 * np.random.default_rng(0).standard_normal(len(x)).astype(np.float32)
    env = envelope_db(x, sr)
    floor, speech, thr = levels(env)
    assert floor < -70 and speech > -15 and floor < thr < speech
    sil = silences(env, thr, 0.3)
    assert len(sil) == 2 and sil[0][1] == pytest.approx(1.0, abs=0.03) and sil[1][0] == pytest.approx(2.0, abs=0.03)


def test_clipping_detects_runs_not_single_peaks():
    x = np.zeros(48000, np.float32)
    x[100] = 1.0                       # single peak: fine
    x[1000:1010] = 1.0                 # flat-topped: clipped
    c = clipping(x, 48000)
    assert c["clipped_runs"] == 1 and c["first_at"] == pytest.approx(1000 / 48000, abs=1e-3)


def test_face_exposure_beats_frame_exposure():
    img = np.full((100, 100, 3), 250, np.uint8)        # white sand everywhere
    img[30:60, 40:60] = 150                             # well-exposed face
    face = {"x": 0.4, "y": 0.3, "w": 0.2, "h": 0.3}
    assert image_quality(img)["clipped_highlights"] > 0.5
    fq = face_quality(img, face)
    assert fq["face_luma"] == pytest.approx(150, abs=1) and fq["face_clipped"] == 0


def test_footage_classification():
    from cutroom.perception.visual import FrameSample

    q = {"sharpness": 100.0}
    face = [{"x": 0.4, "y": 0.2, "w": 0.2, "h": 0.3}]
    th = [FrameSample(float(t), face, q) for t in range(60)]
    assert classify_footage(0.85, [{}], th, 60)["type"] == "talking_head"
    pod = [FrameSample(float(t), face * 2, q) for t in range(60)]
    assert classify_footage(0.9, [{}], pod, 60)["type"] == "podcast"
    screen = [FrameSample(float(t), [], {"sharpness": 800.0}) for t in range(60)]
    assert classify_footage(0.6, [{}], screen, 60)["type"] == "screen_recording"


def test_contact_sheet_writes_labelled_grid(tmp_path):
    frames = [np.full((90, 160, 3), v, np.uint8) for v in (0, 80, 160, 240, 120)]
    out = contact_sheet(frames, [f"f{k}" for k in range(5)], tmp_path / "s.jpg", cols=3, title="test")
    img = cv2.imread(str(out))
    assert img.shape[1] == 3 * 160 and img.shape[0] == 2 * 90 + 32


def test_topics_task_and_check(tmp_path):
    wf = seq("First topic here. Still first. Now the second topic. The end.")
    words = {"S01": wf}
    task = T.prepare(words, tmp_path / "topics.task.md")
    assert "[0]" in task.read_text() and "[3]" in task.read_text()
    good = {"schema": "cutroom.topics/1", "topics": [
        {"id": "t01", "source": "S01", "title": "First", "summary": "s", "first_sentence": 0, "last_sentence": 1,
         "usefulness": 3},
        {"id": "t02", "source": "S01", "title": "Second", "summary": "s", "first_sentence": 2, "last_sentence": 3,
         "usefulness": 4, "short_candidate": True}]}
    p = tmp_path / "topics.json"
    p.write_text(json.dumps(good))
    assert T.check(p, words) == []
    bad = json.loads(json.dumps(good))
    bad["topics"][1]["first_sentence"] = 3
    bad["topics"][0]["usefulness"] = 9
    p.write_text(json.dumps(bad))
    problems = T.check(p, words)
    assert problems and any("usefulness" in x for x in problems)


@needs_ffmpeg
def test_audio_and_visual_reports_on_sample(ingested_job):
    job = ingested_job
    s = job.manifest.sources[0]
    make_proxy(job, s)
    extract_audio(job, s)
    a = P.audio_report(job, s)
    assert -40 < a["integrated_lufs"] < -5 and a["true_peak_dbtp"] < 0
    assert a["clipping"]["clipped_runs"] == 0 and a["silences_over_0_3s"] >= 5
    v = P.visual_report(job, s, speech_coverage=0.7)
    assert v["shot_count"] >= 1 and (job.root / v["overview_sheet"]).is_file()
    assert v["footage_type"]["type"] in {"talking_head", "podcast", "screen_recording", "vlog", "mixed"}
    loud = measure_loudness(job.abspath(s.audio["analysis"]))
    assert loud.integrated_lufs == pytest.approx(a["integrated_lufs"], abs=0.1)


@needs_ffmpeg
def test_frames_command_source_times(ingested_job):
    job = ingested_job
    make_proxy(job, job.manifest.sources[0])
    r = runner.invoke(app, ["frames", job.id, "--source", "S01", "--at", "1", "--at", "5.5", "--at", "10", "--json"])
    assert r.exit_code == 0, r.output
    path = json.loads(r.output)["path"]
    assert cv2.imread(path) is not None
