"""Phase 8: interrupted renders resume from finished work and stay frame-exact; a changed plan
never reuses stale parts; stage timings are reported."""

import json

import numpy as np
import pytest

from conftest import needs_ffmpeg
from test_render import coded_job  # noqa: F401  (fixture)

pytestmark = needs_ffmpeg


class Boom(RuntimeError):
    pass


def _cut(job):
    from cutroom.timeline import store
    from cutroom.timeline.ops import Removal, assemble, remove_ranges

    tl = assemble(job)
    remove_ranges(tl, [Removal("S01", 2.0, 3.0, "manual", "t"), Removal("S01", 5.0, 7.0, "manual", "t")],
                  tool="t", version=2, min_clip=0.1)
    return store.load(job, store.save_new(job, tl, command="t", summary="t"))


def _split_runs(monkeypatch):
    """One encoded run per segment, so an interruption can land between runs."""
    from cutroom.render import engine as E

    monkeypatch.setattr(E, "group_runs", lambda segs: [[s] for s in segs])


def _failing_runs(monkeypatch, fail_at: int | None):
    from cutroom.render import engine as E

    real = E.render_run
    calls = []

    def run(job, tl, run, part, **kw):
        calls.append(run[0].n_in)
        if fail_at is not None and len(calls) == fail_at:
            raise Boom("simulated crash")
        return real(job, tl, run, part, **kw)

    monkeypatch.setattr(E, "render_run", run)
    return calls


@pytest.fixture
def cut_coded(coded_job):
    return coded_job, _cut(coded_job)


def test_render_resumes_after_a_crash_between_runs(cut_coded, monkeypatch):
    from test_render import _frame_lumas

    from cutroom.render.engine import render

    job, tl = cut_coded
    _split_runs(monkeypatch)
    _failing_runs(monkeypatch, fail_at=2)
    with pytest.raises(Boom):
        render(job, tl, mode="preview", timecode=False)
    scratch = job.root / "renders" / f".tmp_v{tl.meta.version:03d}_preview"
    state = json.loads((scratch / "state.json").read_text())
    assert state["runs_done"] == [0] and "audio" in state["stages"]      # finished work kept
    assert "interrupted" in (job.root / "log.md").read_text()
    resumed_calls = _failing_runs(monkeypatch, fail_at=None)
    rep = render(job, tl, mode="preview", timecode=False)
    assert rep["resumed"] is True and all(rep["checks"].values()), rep
    assert len(resumed_calls) == 2 and 0 not in resumed_calls             # run 0 was not encoded again
    assert not scratch.exists()                                            # cleaned once finished
    assert {"audio", "video", "mux"} <= set(rep["timings_s"])
    lum = _frame_lumas(job.root / rep["path"])
    src = list(range(0, 50)) + list(range(75, 125)) + list(range(175, 250))
    assert [int(round((v - 16) / 8)) for v in lum] == [n % 25 for n in src]


def test_a_changed_plan_never_reuses_interrupted_parts(cut_coded, monkeypatch):
    from cutroom.render.engine import render

    job, tl = cut_coded
    _split_runs(monkeypatch)
    _failing_runs(monkeypatch, fail_at=2)
    with pytest.raises(Boom):
        render(job, tl, mode="preview", timecode=False)
    _failing_runs(monkeypatch, fail_at=None)
    rep = render(job, tl, mode="preview", timecode=False, join_fade=0.004)   # different audio plan
    assert "resumed" not in rep and all(rep["checks"].values())


def test_state_from_corrupt_or_foreign_scratch_is_discarded(tmp_path):
    from cutroom.render.engine import _load_state

    scratch = tmp_path / ".tmp_v001_preview"
    scratch.mkdir()
    (scratch / "state.json").write_text("{not json")
    (scratch / "part_000.mp4").write_bytes(b"stale")
    st = _load_state(scratch, "k1")
    assert st == {"key": "k1"} and not (scratch / "part_000.mp4").exists()
    st["stages"] = {"audio": True}
    (scratch / "state.json").write_text(json.dumps(st))
    assert _load_state(scratch, "k1")["stages"] == {"audio": True}
    assert _load_state(scratch, "k2") == {"key": "k2"}


@pytest.mark.slow
def test_overlay_frames_resume_after_a_crash_mid_overlay(monkeypatch, jobs_base, tmp_path):
    """Captions burnt in: a crash after half the overlay frames were drawn redraws only the rest,
    and the result matches a clean render."""
    from test_graphics import _remotion_ready

    if not _remotion_ready():
        pytest.skip("graphics package not installed")
    from test_render import _luma_coded_source

    from cutroom.captions.plan import plan_captions
    from cutroom.ingest.ingest import ingest
    from cutroom.ingest.proxy import extract_audio, make_proxy
    from cutroom.job import Job
    from cutroom.render.engine import render
    from cutroom.style import load_style
    from cutroom.timeline import store
    from test_cutting import seq

    src = tmp_path / "coded.mp4"
    _luma_coded_source(src)
    job = Job.create("resume-overlay")
    ingest(job, [src])
    s = job.manifest.sources[0]
    make_proxy(job, s)
    extract_audio(job, s)
    wf = seq("This is the first sentence here. And the second one follows now. A third short one.", start=0.3,
             word=0.25)
    (job.root / "analysis" / "S01").mkdir(parents=True, exist_ok=True)
    (job.root / "analysis" / "S01" / "words.json").write_text(wf.model_dump_json(by_alias=True))
    tl = _cut(job)
    tl.captions = plan_captions(tl, {"S01": wf}, load_style(), "clean")
    tl = store.load(job, store.save_new(job, tl, command="t", summary="captions"))

    _failing_runs(monkeypatch, fail_at=1)
    with pytest.raises(Boom):
        render(job, tl, mode="preview", timecode=False)
    scratch = job.root / "renders" / f".tmp_v{tl.meta.version:03d}_preview"
    frames = sorted((scratch / "overlay").glob("*.png"))
    assert len(frames) == 175 and (scratch / "overlay.done").is_file()
    for p in frames[80:]:                       # as if the overlay render died at frame 80
        p.unlink()
    (scratch / "overlay.done").unlink()
    _failing_runs(monkeypatch, fail_at=None)
    rep = render(job, tl, mode="preview", timecode=False)
    assert rep["resumed"] and all(rep["checks"].values()), rep
    resumed = _decode(job.root / rep["path"])
    clean = render(job, tl, mode="preview", timecode=False, out_path=job.root / "renders" / "clean.mp4")
    assert "resumed" not in clean
    ref = _decode(job.root / clean["path"])
    assert resumed.shape == ref.shape
    assert np.abs(resumed.astype(int) - ref.astype(int)).mean() < 0.5


def _decode(path) -> np.ndarray:
    import subprocess

    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8)


# --------------------------------------------------------------------------- batch queue


def test_batch_parses_lines_and_refuses_the_human_gate(tmp_path):
    from cutroom.batch import parse_batch
    from cutroom.errors import RefusedError

    items = parse_batch("# night run\ncutroom render a --mode final\n\nshorts a  # trailing comment\n")
    assert [i["argv"] for i in items] == [["render", "a", "--mode", "final"], ["shorts", "a"]]
    assert [i["line"] for i in items] == [2, 4]
    with pytest.raises(RefusedError, match="cannot run from a batch"):
        parse_batch("render a\ncutroom approve a --package x --by me\n")
    with pytest.raises(RefusedError, match="no commands"):
        parse_batch("# nothing\n")


def test_batch_stops_on_failure_and_resumes_without_rerunning_finished_lines(tmp_path):
    from cutroom.batch import run_batch, state_path

    f = tmp_path / "night.txt"
    f.write_text("validate a\nrender a --mode final\nvalidate b\n")
    calls = []

    def flaky(argv):
        calls.append(argv)
        return (1, "Error: render crashed\n") if argv[0] == "render" and len(calls) < 3 else (0, "ok")

    res = run_batch(f, runner=flaky)
    assert (res["ok"], res["failed"], res["not_run"]) == (1, 1, 1) and len(calls) == 2
    assert state_path(f).is_file()
    f.write_text("# added later\nvalidate a\nrender a --mode final\nvalidate b\n")   # lines moved down
    res = run_batch(f, runner=flaky)
    assert (res["ok"], res["skipped"], res["failed"]) == (2, 1, 0)
    assert calls[2:] == [["render", "a", "--mode", "final"], ["validate", "b"]]       # line 1 not again
    res = run_batch(f, runner=flaky)
    assert res["skipped"] == 3 and len(calls) == 4
    assert run_batch(f, runner=flaky, again=True, dry_run=True)["not_run"] == 3


def test_batch_runs_real_commands_in_their_own_process(cut_coded, tmp_path):
    from cutroom.batch import run_batch

    job, _ = cut_coded
    f = tmp_path / "q.txt"
    f.write_text(f"validate {job.id}\nnext {job.id} --json\nvalidate no-such-job\n")
    res = run_batch(f, keep_going=True)
    assert [ln["status"] for ln in res["lines"]] == ["ok", "ok", "FAILED"]


# --------------------------------------------------------------------------- job report


def test_report_lists_time_stages_cost_and_network(cut_coded):
    from cutroom.render.engine import render
    from cutroom.report import write_report

    job, tl = cut_coded
    render(job, tl, mode="preview", timecode=False)
    with job_command_logged(job):
        pass
    r, md = write_report(job)
    assert r["cost"]["usd"] == 0.0 and r["cost"]["paid_services"] == []
    assert r["network"]["client_media_uploaded"] is False
    full = [x for x in r["renders"] if not x["range"]]
    assert full and {"audio", "video", "mux"} <= set(full[0]["timings_s"]) and not full[0]["checks_failed"]
    assert r["time"]["commands"]["validate"]["runs"] == 1
    assert "**$0.00.**" in md and "Client footage uploaded anywhere: **no**" in md
    assert (job.root / "logs" / "report.md").is_file()
    assert any(s["stage"] == "approval (a person)" and not s["done"] for s in r["stages"])


def job_command_logged(job):
    from cutroom.cli_common import job_command

    return job_command(job, "validate")


# --------------------------------------------------------------------------- child processes


def test_an_interrupted_ffmpeg_never_outlives_the_command(monkeypatch, tmp_path):
    """Ctrl-C (here: the progress callback raising) stops FFmpeg itself, so a resumed render
    cannot race an orphan still writing the same part file."""
    from cutroom.system import procs
    from cutroom.system.ffmpeg import run_ffmpeg

    started = []
    real = procs.popen_group
    monkeypatch.setattr(procs, "popen_group", lambda cmd, **kw: started.append(real(cmd, **kw)) or started[-1])

    def interrupt(f):
        if f > 0:
            raise Boom("interrupted")

    with pytest.raises(Boom):
        run_ffmpeg(["-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=600", "-c:v", "libx264", "-preset", "slow",
                    str(tmp_path / "long.mp4")], desc="long", duration=600.0, on_progress=interrupt)
    assert started and started[0].poll() is not None            # FFmpeg is gone, not orphaned


def test_sigterm_stops_the_command_and_its_ffmpeg(jobs_base, tmp_path):
    """`kill` (SIGTERM) is handled like Ctrl-C: the command stops, FFmpeg in its own process group
    is stopped too, and the job log records the failure."""
    import os
    import signal
    import subprocess
    import sys
    import time

    from cutroom.job import Job

    if os.name != "posix":
        pytest.skip("POSIX signals")
    Job.create("sigterm-test")
    marker = f"sigterm_{os.getpid()}.mp4"
    p = subprocess.Popen([sys.executable, "-m", "cutroom", "ffmpeg", "sigterm-test", "--", "-f", "lavfi", "-i",
                          "testsrc2=s=1280x720:r=25:d=600", "-c:v", "libx264", "-preset", "slow", f"renders/{marker}"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy())

    def ffmpeg_running() -> bool:                    # processes named ffmpeg, not the cutroom process
        return marker in subprocess.run(["pgrep", "-a", "ffmpeg"], capture_output=True, text=True).stdout

    t0 = time.time()
    while not ffmpeg_running() and time.time() - t0 < 30:
        time.sleep(0.2)
    assert ffmpeg_running(), p.communicate(timeout=5)
    p.send_signal(signal.SIGTERM)
    assert p.wait(timeout=30) == 130
    time.sleep(0.5)
    assert not ffmpeg_running()
