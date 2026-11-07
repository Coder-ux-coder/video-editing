import stat

import pytest

from cutroom.errors import JobError, MediaError
from cutroom.ingest.ingest import ingest, safe_name
from cutroom.ingest.probe import fps_rational, probe, snap_fps
from cutroom.ingest.proxy import extract_audio, make_proxy, proxy_size
from cutroom.job import Job, list_jobs, validate_job_id

from conftest import needs_ffmpeg


# ----------------------------------------------------------------- pure helpers

@pytest.mark.parametrize("bad", ["A", "ab", "-abc", "abc-", "has space", "UPPER", "a/b"])
def test_job_id_validation_rejects(bad):
    with pytest.raises(JobError):
        validate_job_id(bad)


def test_job_id_validation_accepts():
    assert validate_job_id("acme-2026-11.launch_v2") == "acme-2026-11.launch_v2"


def test_safe_name():
    assert safe_name("My Clip (final) #2.MOV") == "My_Clip_final_2.mov"
    assert len(safe_name("x" * 300 + ".mp4")) <= 80


@pytest.mark.parametrize("w,h,side,expect", [
    (1920, 1080, 540, (960, 540)), (1080, 1920, 540, (540, 960)),
    (640, 360, 540, (640, 360)),          # never upscale
    (3840, 2160, 540, (960, 540)), (1440, 1080, 540, (720, 540)),
])
def test_proxy_size(w, h, side, expect):
    assert proxy_size(w, h, side) == expect


def test_fps_helpers():
    assert snap_fps(29.97003) == 29.97 and snap_fps(24000 / 1001) == 23.976
    assert snap_fps(12.5) == 12.5
    assert fps_rational(29.97) == "30000/1001" and fps_rational(25.0) == "25"


# ----------------------------------------------------------------- jobs

def test_create_and_load_job(jobs_base):
    job = Job.create("demo-job", client="acme", title="Demo")
    for d in ("source", "proxy", "analysis", "versions", "renders", "review", "delivery"):
        assert (job.root / d).is_dir()
    assert (job.root / "brief.md").read_text().startswith("# Brief: demo-job")
    assert "job created" in (job.root / "log.md").read_text()
    again = Job.load("demo-job")
    assert again.manifest.client == "acme"
    assert [m.id for m in list_jobs()] == ["demo-job"]
    with pytest.raises(JobError):
        Job.create("demo-job")
    with pytest.raises(JobError):
        Job.load("nope-job")


# ----------------------------------------------------------------- media

@needs_ffmpeg
def test_probe_sample(sample_mp4, sample_meta):
    info = probe(sample_mp4)
    assert info.kind == "video"
    v, a = info.primary_video, info.primary_audio
    assert (v.display_width, v.display_height) == (640, 360) and v.fps == 25.0
    assert not v.likely_vfr and not v.is_hdr
    assert a.sample_rate == 48000 and a.channels == 1
    assert abs(info.duration - sample_meta["duration"]) < 0.1
    assert info.aspect_label == "16:9"


@needs_ffmpeg
def test_probe_rejects_non_media(tmp_path):
    bad = tmp_path / "notes.mp4"
    bad.write_text("not a video")
    with pytest.raises(MediaError) as e:
        probe(bad)
    assert e.value.fix


@needs_ffmpeg
def test_ingest_copies_read_only_and_never_touches_original(jobs_base, sample_mp4):
    before = (sample_mp4.stat().st_mtime, sample_mp4.stat().st_size, stat.S_IMODE(sample_mp4.stat().st_mode))
    job = Job.create("ingest-test")
    res = ingest(job, [sample_mp4])
    assert [r.status for r in res] == ["added"]
    src = job.manifest.sources[0]
    assert src.id == "S01" and src.copied and src.hash.startswith("blake2b:")
    copied = job.abspath(src.path)
    assert copied.is_file()
    assert not (copied.stat().st_mode & stat.S_IWUSR)  # read-only (mode bits; root ignores access())
    assert (sample_mp4.stat().st_mtime, sample_mp4.stat().st_size, stat.S_IMODE(sample_mp4.stat().st_mode)) == before
    # Same file again is a duplicate, not a second source.
    res2 = ingest(job, [sample_mp4])
    assert res2[0].status == "duplicate" and len(Job.load("ingest-test").manifest.sources) == 1
    assert "S01" in (job.root / "log.md").read_text()


@needs_ffmpeg
def test_ingest_reference_mode_and_bad_file(jobs_base, sample_mp4, tmp_path):
    bad = tmp_path / "broken.mov"
    bad.write_bytes(b"\x00" * 1000)
    job = Job.create("ref-test")
    res = ingest(job, [sample_mp4, bad], copy=False)
    assert [r.status for r in res] == ["added", "failed"]
    src = job.manifest.sources[0]
    assert not src.copied and job.abspath(src.path) == sample_mp4.resolve()
    assert "Could not ingest" in (job.root / "log.md").read_text()


@needs_ffmpeg
def test_proxy_and_audio(ingested_job, sample_meta):
    job = ingested_job
    src = job.manifest.sources[0]
    r = make_proxy(job, src, short_side=540)
    assert r["status"] == "created" and r["size"] == "640x360" and r["duration_drift_s"] < 0.1
    p = probe(job.abspath(src.proxy))
    assert p.primary_video.fps == 25.0 and p.primary_audio is not None
    # Idempotent.
    assert make_proxy(job, src, short_side=540)["status"] == "up-to-date"
    a = extract_audio(job, src)
    assert a["status"] == "created"
    wav = probe(job.abspath(src.audio["asr"]))
    assert wav.primary_audio.sample_rate == 16000 and wav.primary_audio.channels == 1
    assert abs(wav.duration - sample_meta["duration"]) < 0.1
    flac = probe(job.abspath(src.audio["analysis"]))
    assert flac.primary_audio.sample_rate == 48000
    assert extract_audio(job, src)["status"] == "up-to-date"


def test_atomic_write_gives_normal_permissions(tmp_path):
    import os

    from cutroom.job import atomic_write_text

    p = tmp_path / "x.json"
    atomic_write_text(p, "{}")
    assert p.read_text() == "{}"
    if os.name != "nt":
        assert stat.S_IMODE(p.stat().st_mode) & stat.S_IRGRP  # readable by group, not 0600
    assert not list(tmp_path.glob(".x.json.*.tmp"))
