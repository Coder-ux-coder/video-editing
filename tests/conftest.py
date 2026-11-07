from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_MP4 = ROOT / "assets" / "samples" / "doctor_sample.mp4"
SAMPLE_META = json.loads((ROOT / "assets" / "samples" / "doctor_sample.json").read_text(encoding="utf-8"))

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None and not (ROOT / "tools" / "ffmpeg").exists(),
                                  reason="FFmpeg not installed")


@pytest.fixture
def jobs_base(tmp_path, monkeypatch) -> Path:
    """Isolated jobs folder so tests never touch the real jobs/."""
    base = tmp_path / "jobs"
    base.mkdir()
    monkeypatch.setenv("CUTROOM_JOBS_DIR", str(base))
    return base


@pytest.fixture
def sample_mp4() -> Path:
    return SAMPLE_MP4


@pytest.fixture
def sample_meta() -> dict:
    return SAMPLE_META


@pytest.fixture
def ingested_job(jobs_base, sample_mp4):
    from cutroom.ingest.ingest import ingest
    from cutroom.job import Job

    job = Job.create("test-job", client="acme")
    ingest(job, [sample_mp4])
    return job
