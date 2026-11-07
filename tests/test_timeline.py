"""Timeline model, edit operations, version store, diff and outline."""

import json

import pytest
from typer.testing import CliRunner

from cutroom.cli import app
from cutroom.errors import JobError, RefusedError
from cutroom.timeline import store
from cutroom.timeline.diff import diff, diff_text
from cutroom.timeline.model import Clip, Meta, SourceRef, Timeline, validate_timeline
from cutroom.timeline.ops import Removal, check_cut_points, kept_ranges, pack, remove_ranges, restore_cut
from cutroom.timeline.outline import cut_list, outline, summary, tc

from conftest import needs_ffmpeg
from test_cutting import seq

runner = CliRunner()


def make_tl(duration: float = 20.0) -> Timeline:
    tl = Timeline(meta=Meta(job_id="t-job", fps=25.0, width=1920, height=1080),
                  sources=[SourceRef(id="S01", path="source/S01.mp4", hash="x", duration=duration)],
                  clips=[Clip(id="c0001", source="S01", src_in=0.0, src_out=duration, reason="assembly")])
    pack(tl)
    return tl


def test_tc_formatting():
    assert tc(0) == "00:00.000" and tc(61.5) == "01:01.500" and tc(3725.25) == "01:02:05.250"


def test_remove_ranges_splits_and_ripples():
    tl = make_tl()
    applied = remove_ranges(tl, [Removal("S01", 5.0, 7.0, "silence", "pause"),
                                 Removal("S01", 12.0, 13.0, "filler", "um")], tool="t", version=2, min_clip=0.1)
    assert [c.kind for c in applied] == ["silence", "filler"]
    clips = tl.track_clips()
    assert [(c.src_in, c.src_out) for c in clips] == [(0.0, 5.0), (7.0, 12.0), (13.0, 20.0)]
    assert [c.start for c in clips] == [0.0, 5.0, 10.0]          # rippled, no gaps
    assert tl.duration == pytest.approx(17.0)
    assert clips[0].id == "c0001" and len({c.id for c in clips}) == 3
    assert not validate_timeline(tl)


def test_overlapping_removals_count_once_and_respect_previous_cuts():
    tl = make_tl()
    a = remove_ranges(tl, [Removal("S01", 5.0, 8.0, "silence", "a"), Removal("S01", 7.0, 9.0, "filler", "b")],
                      tool="t", version=2, min_clip=0.1)
    assert sum(c.duration for c in a) == pytest.approx(4.0)
    b = remove_ranges(tl, [Removal("S01", 4.0, 10.0, "manual", "c")], tool="t", version=3, min_clip=0.1)
    assert sum(c.duration for c in b) == pytest.approx(2.0)     # only what was still there
    assert tl.duration == pytest.approx(14.0)


def test_locked_clips_are_never_changed():
    tl = make_tl()
    tl.clips[0].locked = True
    assert remove_ranges(tl, [Removal("S01", 2.0, 3.0, "manual", "x")], tool="t", version=2) == []
    assert tl.duration == 20.0


def test_orphan_fragment_without_words_is_dropped():
    tl = make_tl()
    wf = seq("hello there friend", start=1.0)       # words 1.0-1.9
    applied = remove_ranges(tl, [Removal("S01", 2.0, 5.0, "silence", "a"), Removal("S01", 5.2, 9.0, "silence", "b")],
                            tool="t", version=2, words={"S01": wf}, min_clip=0.4)
    assert "orphan" in [c.kind for c in applied]
    assert all(c.src_in < 5.0 or c.src_in >= 9.0 for c in tl.track_clips())


def test_restore_cut_merges_back():
    tl = make_tl()
    cut = remove_ranges(tl, [Removal("S01", 5.0, 7.0, "silence", "x")], tool="t", version=2, min_clip=0.1)[0]
    restore_cut(tl, cut.id)
    assert [(c.src_in, c.src_out) for c in tl.track_clips()] == [(0.0, 20.0)]
    assert tl.cuts[0].restored and not tl.active_cuts()
    with pytest.raises(RefusedError):
        restore_cut(tl, cut.id)


def test_cut_point_check_flags_mid_word():
    tl = make_tl()
    wf = seq("one two three", start=1.0)            # "two" = 1.3-1.6
    remove_ranges(tl, [Removal("S01", 1.45, 3.0, "manual", "x")], tool="t", version=2, min_clip=0.1)
    problems = check_cut_points(tl, {"S01": wf})
    assert problems and "two" in problems[0]


def test_validate_detects_overlap_and_bad_ranges():
    tl = make_tl()
    tl.clips.append(Clip(id="c0002", source="S01", src_in=0.0, src_out=30.0, start=1.0))
    problems = validate_timeline(tl)
    assert any("overlap" in p for p in problems) and any("outside" in p for p in problems)


def test_outline_and_cut_list_are_readable():
    tl = make_tl()
    wf = seq("Hello there. um This is the rest of it.", start=1.0, gaps={2: 2.0})
    remove_ranges(tl, [Removal("S01", 2.0, 3.6, "silence", "1.9s pause shortened")], tool="t", version=2,
                  words={"S01": wf}, min_clip=0.1)
    text = outline(tl, {"S01": wf})
    assert "Hello there." in text and "✂" in text and "pause shortened" in text
    assert "silence" in cut_list(tl)
    assert summary(tl)["cuts_by_kind"]["silence"]["count"] == 1


# ------------------------------------------------------------------ store (needs a job)

@pytest.fixture
def tl_job(jobs_base):
    from cutroom.job import Job

    job = Job.create("tl-job")
    return job


def test_versions_are_immutable_and_undo_branches(tl_job):
    job = tl_job
    tl = make_tl()
    tl.meta.job_id = job.id
    v1 = store.save_new(job, tl, command="assemble", summary="a")
    remove_ranges(tl, [Removal("S01", 1.0, 2.0, "manual", "x")], tool="t", version=2, min_clip=0.1)
    v2 = store.save_new(job, tl, command="cut", summary="b")
    assert (v1, v2) == (1, 2) and store.resolve(job, "HEAD~1") == 1
    assert store.undo(job) == 1 and store.head(job) == 1
    tl3 = store.load(job)
    remove_ranges(tl3, [Removal("S01", 5.0, 6.0, "manual", "y")], tool="t", version=3, min_clip=0.1)
    v3 = store.save_new(job, tl3, command="cut", summary="c")
    assert store.load(job, v3).meta.parent == 1                  # branched from v001
    assert store.load(job, 2).duration == pytest.approx(19.0)     # v002 kept on disk
    assert [e.version for e in store.load(job, 3).meta.changelog] == [1, 3]
    log = (job.root / "versions" / "CHANGELOG.md").read_text()
    assert "v001" in log and "v002" in log and "v003" in log
    with pytest.raises(RefusedError):
        store.undo(job) and store.undo(job)
    with pytest.raises(JobError):
        store.resolve(job, "v099")


def test_invalid_timeline_is_refused(tl_job):
    tl = make_tl()
    tl.clips.append(Clip(id="c0001", source="S01", src_in=0.0, src_out=1.0))   # duplicate id
    with pytest.raises(RefusedError):
        store.save_new(tl_job, tl, command="x", summary="x")


def test_diff_reports_cuts_and_duration():
    a = make_tl()
    a.meta.version = 1
    b = a.model_copy(deep=True)
    b.meta.version = 2
    remove_ranges(b, [Removal("S01", 1.0, 3.0, "silence", "pause")], tool="t", version=2, min_clip=0.1)
    d = diff(a, b)
    assert d["duration"] == [20.0, 18.0] and len(d["cuts_added"]) == 1
    assert "-2.00s" in diff_text(a, b) and "silence" in diff_text(a, b)


# ------------------------------------------------------------------ CLI on a real (synthetic) clip

@needs_ffmpeg
@pytest.mark.slow
def test_cli_rough_cut_on_sample(jobs_base, sample_mp4, sample_meta):
    from cutroom.perception.models import is_downloaded
    from cutroom.system.profile import load_profile

    if not is_downloaded(load_profile().asr.model):
        pytest.skip("speech model not downloaded")
    for args in (["new", "rc-job"], ["ingest", "rc-job", str(sample_mp4)], ["proxy", "rc-job"]):
        assert runner.invoke(app, args).exit_code == 0
    r = runner.invoke(app, ["rough-cut", "rc-job", "--json"])
    assert r.exit_code == 0, r.output
    res = json.loads(r.output)          # exactly one JSON object, even though 5 steps ran
    checks = res["render"]["checks"]
    assert {"duration_ok", "frames_ok", "no_clicks", "no_edit_lists"} <= set(checks) and all(checks.values())
    # Retake (2.0 s pause + repeated sentence) and stumble are both gone; everything else stays.
    out = runner.invoke(app, ["show", "rc-job"]).output
    spoken = "\n".join(line for line in out.splitlines()
                       if "✂" not in line and not line.startswith("#"))   # clip text only
    assert spoken.count("let the dough rest") == 1 and "retake" in out and "stumble" in out
    assert "⟪quiet" not in spoken
    assert runner.invoke(app, ["validate", "rc-job"]).exit_code == 0
    assert 15 < res["duration_s"] < 20
    versions = runner.invoke(app, ["versions", "rc-job", "--json"]).output
    assert [v["command"] for v in json.loads(versions)["versions"]][:2] == ["assemble", "remove-silence"]
    d = runner.invoke(app, ["diff", "rc-job", "v001"]).output
    assert "→" in d and "cuts" in d
