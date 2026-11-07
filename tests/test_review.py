"""Review loop: each automatic check finds its seeded defect; scorecard; fix ordering; report."""

import pytest

from cutroom.captions.plan import plan_captions
from cutroom.review.checks import (Issue, ReviewInput, check_audio, check_captions, check_cuts, check_graphics,
                                   check_structure, flash_shots, run_checks, scorecard)
from cutroom.style import load_style
from cutroom.timeline.model import Graphic, Keyframe
from cutroom.timeline.ops import Removal, extend_clip_edge, remove_ranges

from test_cutting import seq
from test_timeline import make_tl

STYLE = load_style()
TEXT = ("We found the tracks in every layer. Um the oldest ones are very deep. "
        "That changes the story of the Americas. It really does.")


def _ri(tl, wf, tmp_path, **kw):
    return ReviewInput(tl=tl, words={"S01": wf}, style=STYLE, job_root=tmp_path, **kw)


def _clean(tmp_path, gaps=None):
    """A timeline that should pass the checks: captions planned, voice chain set, no long pauses."""
    wf = seq(TEXT, start=0.3, word=0.3, gaps=gaps or {})
    tl = make_tl(round(wf.words[-1].end + 0.4, 2))
    tl.audio.voice_chain = {"target_lufs": -14.0, "stages": []}
    return tl, wf


def checks_of(issues):
    return {i.check for i in issues}


def test_long_pause_and_filler_left_in_are_found_and_auto_fixable(tmp_path):
    tl, wf = _clean(tmp_path, gaps={7: 1.8})            # a long pause before "Um"
    issues = check_audio(_ri(tl, wf, tmp_path))
    found = {i.check: i for i in issues}
    assert "audio.long_pauses" in found and found["audio.long_pauses"].auto
    assert "audio.fillers" in found and "Um" in found["audio.fillers"].evidence
    tl.audio.voice_chain = {}
    assert "audio.no_chain" in checks_of(check_audio(_ri(tl, wf, tmp_path)))


def test_loudness_and_peak_from_the_render_report(tmp_path):
    tl, wf = _clean(tmp_path)
    rep = {"loudness": {"integrated_lufs": -19.5, "true_peak_dbtp": -0.2}, "checks": {}, "clicks": []}
    found = checks_of(check_audio(_ri(tl, wf, tmp_path, render=rep)))
    assert {"audio.loudness", "audio.true_peak"} <= found


def test_stale_and_missing_captions(tmp_path):
    tl, wf = _clean(tmp_path)
    assert "captions.missing" in checks_of(check_captions(_ri(tl, wf, tmp_path)))
    tl.captions = plan_captions(tl, {"S01": wf}, STYLE, "clean")
    assert not check_captions(_ri(tl, wf, tmp_path))
    remove_ranges(tl, [Removal("S01", 3.3, 5.6, "manual", "x")], tool="t", version=2, min_clip=0.1)
    stale = [i for i in check_captions(_ri(tl, wf, tmp_path)) if i.check == "captions.stale"]
    assert stale and stale[0].severity == "blocker" and stale[0].auto and stale[0].data["style"] == "clean"
    # same words, new timing (a pause put back): still stale
    tl2, wf2 = _clean(tmp_path, gaps={9: 1.5})
    tl2.captions = plan_captions(tl2, {"S01": wf2}, STYLE, "clean")
    remove_ranges(tl2, [Removal("S01", wf2.words[8].end + 0.3, wf2.words[9].start - 0.3, "silence", "x")],
                  tool="t", version=2, min_clip=0.1)
    timing = [i for i in check_captions(_ri(tl2, wf2, tmp_path)) if i.check == "captions.stale"]
    assert timing and timing[0].severity == "major" and "timing" in timing[0].evidence


def test_flash_shot_found_with_a_safe_fix(tmp_path):
    tl, wf = _clean(tmp_path, gaps={7: 1.0})
    pause_start = wf.words[6].end                         # 1 s pause, then "Um"
    shots = {"S01": [round(pause_start + 0.35, 3)]}       # the source cuts to another camera in the pause
    remove_ranges(tl, [Removal("S01", pause_start + 0.55, wf.words[7].start - 0.1, "silence", "x")],
                  tool="t", version=2, min_clip=0.05)
    fl = flash_shots(_ri(tl, wf, tmp_path, shots=shots))
    assert len(fl) == 1 and fl[0]["where"] == "tail" and fl[0]["duration"] == pytest.approx(0.2, abs=0.01)
    assert fl[0]["auto"]                                  # no word in the sliver: it can be trimmed
    assert "cuts.flash_shot" in checks_of(check_cuts(_ri(tl, wf, tmp_path, shots=shots)))


def test_extend_clip_edge_only_over_removed_pause(tmp_path):
    tl, wf = _clean(tmp_path, gaps={7: 1.0})
    a = wf.words[6].end + 0.2
    remove_ranges(tl, [Removal("S01", a, a + 0.6, "silence", "x")], tool="t", version=2, min_clip=0.05)
    first = tl.track_clips()[0]
    before = tl.duration
    extend_clip_edge(tl, first.id, "out", 0.2, words={"S01": wf})
    assert tl.duration == pytest.approx(before + 0.2, abs=0.05)
    with pytest.raises(Exception):
        extend_clip_edge(tl, first.id, "out", 0.6, words={"S01": wf})     # more than was removed


def test_undisguised_mid_sentence_jump_cut(tmp_path):
    tl, wf = _clean(tmp_path)
    w = wf.words
    remove_ranges(tl, [Removal("S01", w[2].end + 0.02, w[4].start - 0.02, "manual", "x")], tool="t", version=2, min_clip=0.1)
    found = [i for i in check_cuts(_ri(tl, wf, tmp_path)) if i.check == "cuts.jump_cuts"]
    assert found and found[0].auto
    tl.track_clips()[1].reframe = [Keyframe(t=0, scale=1.12)]
    assert "cuts.jump_cuts" not in checks_of(check_cuts(_ri(tl, wf, tmp_path)))


def test_graphics_problems(tmp_path):
    tl, wf = _clean(tmp_path)
    tl.graphics = [
        Graphic(id="g0001", template="lower-third", params={"name": "A"}, start=1.0, end=1.4,
                attach={"source": "S01", "t": 1.0, "detached": True}),
        Graphic(id="g0002", template="number-counter", params={"to": 9}, start=3.0, end=6.0,
                fact_check={"unsupported_numbers": [{"param": "to", "value": 9}]}),
    ]
    rep = {"graphics": {"measured": {"g0002": {"head_covered": 0.2, "frames_outside_safe_area": 0, "frames_checked": 9}}}}
    found = {i.check: i for i in check_graphics(_ri(tl, wf, tmp_path, render=rep))}
    assert found["graphics.unsourced"].severity == "blocker"
    assert {"graphics.detached", "graphics.too_short", "graphics.face", "graphics.no_source"} <= set(found)


def test_structure_ending_mid_sentence_and_scorecard(tmp_path):
    tl, wf = _clean(tmp_path)
    remove_ranges(tl, [Removal("S01", wf.words[-2].end + 0.01, tl.duration, "manual", "x")], tool="t", version=2,
                  min_clip=0.1)
    issues = check_structure(_ri(tl, wf, tmp_path))
    assert "structure.ending" in checks_of(issues)
    sc = scorecard(issues + [Issue("x", "audio", "minor", "t", "e")])
    assert not sc["ready"] and sc["categories"]["structure"] == 8.0 and sc["categories"]["audio"] == 9.5


def test_clean_timeline_is_ready(tmp_path):
    tl, wf = _clean(tmp_path)
    tl.captions = plan_captions(tl, {"S01": wf}, STYLE, "clean")
    issues = run_checks(_ri(tl, wf, tmp_path))
    assert scorecard(issues)["ready"], [i.as_dict() for i in issues if i.severity in ("blocker", "major")]
