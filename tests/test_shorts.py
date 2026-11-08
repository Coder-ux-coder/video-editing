"""Shorts: moment scoring, vertical framing (still / moving / fill / camera changes), face mapping,
derived jobs, publishing checks, and frame-exact vertical renders."""

import json

import pytest

from cutroom.shorts.find import Candidate, TSentence, find_candidates, pick, score_window
from cutroom.shorts.reframe import crop_half_width, output_face_mapper, plan_clip, plan_clip_shots
from cutroom.style import load_style

CFG = load_style()["shorts"]
HALF = crop_half_width(1920, 1080, 1080, 1920)


def faces(xs, t0=0.0, w=0.12):
    return [{"t": t0 + k, "faces": ([{"x": x - w / 2, "y": 0.2, "w": w, "h": 0.25}] if x is not None else [])}
            for k, x in enumerate(xs)]


def test_crop_window_geometry():
    assert HALF == pytest.approx((1080 * 9 / 16) / 1920 / 2)       # 607.5 px of 1920, halved


def test_steady_face_gets_a_still_crop_on_the_face():
    f = plan_clip(faces([0.62, 0.63, 0.62, 0.61, 0.62]), 0.0, 4.5, HALF, CFG["reframe"])
    assert f.mode == "crop" and len(f.keyframes) == 1 and f.keyframes[0][1] == pytest.approx(0.62, abs=0.01)


def test_moving_face_is_followed_slowly_and_clamped():
    xs = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    f = plan_clip(faces(xs), 0.0, 6.5, HALF, CFG["reframe"])
    assert f.mode == "crop" and len(f.keyframes) > 2
    xsk = [x for _, x in f.keyframes]
    steps = [abs(b - a) for a, b in zip(xsk, xsk[1:])]
    assert max(steps) <= CFG["reframe"]["max_speed"] * 1.0 + 1e-6      # speed limit per 1 s sample
    assert min(xsk) >= HALF - 1e-6 and max(xsk) <= 1 - HALF + 1e-6        # window stays inside the frame


def test_no_face_or_two_shot_uses_fill_layout():
    assert plan_clip(faces([None, None, 0.5, None]), 0, 3.5, HALF, CFG["reframe"]).mode == "fill"
    two = [{"t": k, "faces": [{"x": 0.2, "y": 0.3, "w": 0.1, "h": 0.2}, {"x": 0.7, "y": 0.3, "w": 0.1, "h": 0.2}]}
           for k in range(5)]
    assert plan_clip(two, 0, 4.5, HALF, CFG["reframe"]).reason == "two people in frame"


def test_framing_jumps_at_a_camera_change_instead_of_panning():
    samples = faces([0.4, 0.4, 0.4, 0.7, 0.7, 0.7])
    f = plan_clip_shots(samples, 0.0, 5.5, HALF, CFG["reframe"], shots=[2.5])
    xs = [x for _, x in f.keyframes]
    ts = [t for t, _ in f.keyframes]
    assert xs[0] == pytest.approx(0.4, abs=0.01) and xs[-1] == pytest.approx(0.7, abs=0.01)
    jump = next(i for i in range(1, len(xs)) if abs(xs[i] - xs[i - 1]) > 0.1)
    assert ts[jump] - ts[jump - 1] < 0.05                 # one frame apart: a cut, not a pan


def _s(text, start, end, idx=None):
    return TSentence(text=text, start=start, end=end, words=len(text.split()), source="S01", src_start=start,
                     src_end=end, index=idx)


def test_hook_scoring_prefers_self_contained_openings():
    strong = score_window([_s("Why did the oldest tracks survive here?", 0, 3), _s("Because mud preserved them.", 3, 25)], [], CFG)
    weak = score_window([_s("That's the first one we found.", 0, 3), _s("It was amazing.", 3, 25)], [], CFG)
    pron = score_window([_s("What does that say about their lives?", 0, 3), _s("We do not know yet.", 3, 25)], [], CFG)
    assert strong.score > pron.score > weak.score
    assert any("needs earlier context" in r for r in weak.reasons)
    q_end = score_window([_s("The finding is clear.", 0, 3), _s("Did they hunt?", 3, 25)], [], CFG)
    assert q_end.parts["ending"] < 0 and "ends on an open question" in q_end.reasons


def test_candidates_respect_length_and_do_not_overlap():
    sents = [_s(f"Sentence number {k} is here.", k * 6.0, k * 6.0 + 5.5, k) for k in range(30)]
    cands = find_candidates(sents, [], {**CFG, "min_s": 20, "max_s": 40})
    assert cands and all(20 <= c.duration <= 40 for c in cands)
    chosen = pick(cands, 4)
    for a in chosen:
        for b in chosen:
            if a is not b:
                assert min(a.end, b.end) - max(a.start, b.start) <= 0.15 * min(a.duration, b.duration) + 1e-6


def test_hook_text_keeps_the_speakers_words():
    from cutroom.tools.shorts_tools import _hook_text

    assert _hook_text("Did humans cause the extinction?") == "Did humans cause the extinction?"
    long = "It's interesting because, from an archaeological standpoint, people were here longer than we thought."
    h = _hook_text(long)
    assert len(h) <= 48 and long.startswith(h.rstrip("…").rstrip(","))


def test_vertical_crop_expression_follows_keyframes():
    from cutroom.render.engine import Segment, vertical_crop_filter

    run = [Segment("a", "S01", 0, 50, 0, None, ("crop", [(0, 0.6)])),
           Segment("b", "S01", 60, 120, 50, None, ("crop", [(0, 0.4), (29, 0.4), (30, 0.7)]))]
    f = vertical_crop_filter(run, 540, 960)
    assert "between(n\\,0\\,49)*(0.60000)" in f
    assert "between(n\\,79\\,79)" in f and "between(n\\,80\\,109)*(0.70000)" in f
    assert f.startswith("crop=w='trunc(ih*540/960/2)*2'")


# ------------------------------------------------------------------ derived job, publishing, render


@pytest.fixture
def parent_job(jobs_base, tmp_path):
    from test_render import _luma_coded_source

    from cutroom.ingest.ingest import ingest
    from cutroom.ingest.proxy import extract_audio, make_proxy
    from cutroom.job import Job
    from cutroom.timeline import store
    from cutroom.timeline.ops import assemble

    src = tmp_path / "coded.mp4"
    _luma_coded_source(src)
    job = Job.create("shorts-parent", client="acme")
    ingest(job, [src])
    s = job.manifest.sources[0]
    make_proxy(job, s)
    extract_audio(job, s)
    adir = job.root / "analysis" / "S01"
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "faces.json").write_text(json.dumps(
        [{"t": float(t), "faces": [{"x": 0.6, "y": 0.2, "w": 0.1, "h": 0.2}]} for t in range(11)]))
    store.save_new(job, assemble(job), command="assemble", summary="test")
    (job.root / "brief.md").write_text("# Brief\nCredit: Footage: Test Archive (public domain)\n", encoding="utf-8")
    return job


def test_derived_short_job_shares_media_and_analysis(parent_job):
    from cutroom.job import Job
    from cutroom.shorts.derive import derive_short
    from cutroom.timeline import store

    child = derive_short(parent_job, 1, "S01", 2.0, 7.0, k=1)
    assert child.id == "shorts-parent.short-01" and child.manifest.parent == "shorts-parent"
    tl = store.load(child)
    assert (tl.meta.width, tl.meta.height, tl.meta.aspect) == (1080, 1920, "9:16")
    assert tl.duration == pytest.approx(5.0, abs=0.05)
    assert Job.load(child.id).source_path(child.manifest.sources[0]) == parent_job.source_path(parent_job.manifest.sources[0])
    assert (child.root / "analysis" / "S01" / "faces.json").is_file()
    assert tl.clips[0].reframe and tl.clips[0].reframe[0].x == pytest.approx(0.65, abs=0.01)
    assert "Credit:" in (child.root / "brief.md").read_text(encoding="utf-8")
    m = output_face_mapper(tl)(1.0, {"x": 0.6, "y": 0.2, "w": 0.1, "h": 0.2})
    assert 0 < m["x"] < m["x"] + m["w"] < 1                          # the face sits inside the crop


def test_publish_check(parent_job, monkeypatch):
    from cutroom.shorts import publish as P
    from cutroom.shorts.derive import derive_short

    child = derive_short(parent_job, 1, "S01", 2.0, 7.0, k=2)
    monkeypatch.setattr(P, "short_text", lambda job: "people were here twenty three thousand years ago")
    good = {"title": "People were here 23,000 years ago", "hashtags": ["#Shorts", "#Archaeology", "#Fossils"],
            "description": "Fossil footprints. Footage: Test Archive (public domain)"}
    (child.root / "publish.json").write_text(json.dumps(good), encoding="utf-8")
    assert P.check_publish(child)["ok"]
    bad = {**good, "title": "SHOCKING: people were here 50,000 years ago", "hashtags": ["#viral"],
           "description": "no credit"}
    (child.root / "publish.json").write_text(json.dumps(bad), encoding="utf-8")
    probs = " | ".join(P.check_publish(child)["problems"])
    assert "50000" in probs and "#Shorts" in probs and "all-caps" in probs and "credit" in probs and "hype" in probs


@pytest.mark.slow
def test_vertical_render_is_frame_exact_in_crop_and_fill(parent_job):
    from test_render import _frame_lumas

    from cutroom.render.engine import render
    from cutroom.shorts.derive import derive_short
    from cutroom.shorts.reframe import FILL
    from cutroom.timeline import store
    from cutroom.timeline.model import Effect
    from cutroom.timeline.ops import Removal, remove_ranges

    child = derive_short(parent_job, 1, "S01", 0.0, 10.0, k=3)
    tl = store.load(child).model_copy(deep=True)
    remove_ranges(tl, [Removal("S01", 2.0, 3.0, "manual", "t"), Removal("S01", 5.0, 7.0, "manual", "t")],
                  tool="t", version=2, min_clip=0.1)
    tl.track_clips()[1].effects = [Effect(name=FILL)]               # middle clip: blurred-fill layout
    n = store.save_new(child, tl, command="t", summary="t")
    rep = render(child, store.load(child, n), mode="preview", timecode=False)
    assert rep["size"] == "540x960" and rep["checks"]["frames_ok"] and rep["runs"] == 3
    lum = _frame_lumas(child.root / rep["path"])
    src_frames = list(range(0, 50)) + list(range(75, 125)) + list(range(175, 250))
    assert [int(round((v - 16) / 8)) for v in lum] == [k % 25 for k in src_frames]
