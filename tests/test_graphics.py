"""Motion graphics: template specs, parameter validation, fact checking, placement off faces and
captions, graphics following edits, punch-ins, previews (visual regression) and a real render."""

import json
import os
import re
import shutil
from pathlib import Path

import numpy as np
import pytest

from cutroom.errors import RefusedError
from cutroom.graphics.facts import check_facts, numbers_in, require_facts, supported
from cutroom.graphics.overlay import hide_captions_under
from cutroom.graphics.placement import caption_band, candidate_box, face_cover, head_box, place
from cutroom.graphics.templates import get_template, load_templates, min_hold, validate_params
from cutroom.paths import project_root
from cutroom.style import load_style
from cutroom.timeline.model import Graphic, timeline_warnings, validate_timeline
from cutroom.timeline.ops import Removal, move_range, remove_ranges, sync_graphics

from test_cutting import seq
from test_timeline import make_tl

STYLE = load_style()
THEME = STYLE["graphics"]["theme"]
SAFE = THEME["safe"]
GOLDEN = Path(__file__).parent / "golden" / "graphics"


# ------------------------------------------------------------------ templates


def test_every_template_spec_is_valid_and_has_a_component():
    specs = load_templates()
    assert len(specs) >= 14
    index = (project_root() / "graphics" / "src" / "components" / "graphics" / "index.js").read_text(encoding="utf-8")
    registered = set(re.search(r"COMPONENTS = \{([^}]*)\}", index, re.S).group(1).replace("\n", " ").replace("\t", "").split(", "))
    registered = {r.strip() for r in registered}
    for s in specs.values():
        assert s.component in registered, f"{s.id}: component {s.component} not in the Remotion registry"
        assert s.variants and s.description
        if s.zone in ("lower", "side", "corner"):
            assert s.anchors and s.size_hint, f"{s.id} needs anchors and a size hint to be placed"


def test_parameters_are_typed_and_refused_when_wrong():
    lt = get_template("lower-third")
    p, v = validate_params(lt, {"name": " Dan Odess ", "title": "Paleontologist"}, None)
    assert p == {"name": "Dan Odess", "title": "Paleontologist"} and v == "bar"
    with pytest.raises(RefusedError):
        validate_params(lt, {"title": "no name"}, None)                 # required
    with pytest.raises(RefusedError):
        validate_params(lt, {"name": "x" * 41}, None)                   # too long to read
    with pytest.raises(RefusedError):
        validate_params(lt, {"name": "A", "colour": "red"}, None)       # unknown parameter
    with pytest.raises(RefusedError):
        validate_params(lt, {"name": "A"}, "sparkles")                  # unknown variant
    chart = get_template("chart")
    p, _ = validate_params(chart, {"data": "2019:12, 2020:30.5"}, "line")
    assert p["data"] == [{"label": "2019", "value": 12.0}, {"label": "2020", "value": 30.5}]
    lst, _ = validate_params(get_template("list-reveal"), {"items": "One | Two |Three"}, None)
    assert lst["items"] == ["One", "Two", "Three"]
    co, _ = validate_params(get_template("callout"), {"rect": "0.1,0.2,0.3,0.25"}, None)
    assert co["rect"] == {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.25}
    with pytest.raises(RefusedError):
        validate_params(get_template("callout"), {"rect": "0.9,0.2,0.3,0.25"}, None)   # off the frame
    with pytest.raises(RefusedError):
        validate_params(get_template("transition"), {"direction": "up"}, "whip")


def test_reading_time_rule():
    lt = get_template("lower-third")
    rules = STYLE["graphics"]["rules"]
    assert min_hold(lt, {"name": "Dan Odess", "title": "Paleontologist"}, rules) == pytest.approx(1.0 + 0.3 * 3 / 3)
    assert min_hold(get_template("transition"), {}, rules) == 0.0


# ------------------------------------------------------------------ facts


@pytest.mark.parametrize("text,value", [
    ("about 1.5 million people", 1_500_000), ("one point five million", 1.5), ("fifty percent", 50),
    ("three hundred and fifty two tracks", 352), ("we had 1,200 visitors", 1200), ("twenty-five thousand years", 25000),
    ("a hundred years ago", 100), ("nineteen ninety-eight", 1998), ("twenty three thousand years", 23000),
    ("23,000 years", 23000), ("$40", 40), ("30%", 30)])
def test_spoken_and_written_numbers(text, value):
    assert supported(value, numbers_in(text)), (text, numbers_in(text))


def test_rounding_is_allowed_but_not_inflation():
    assert supported(2.4, numbers_in("2.37 million"))
    assert not supported(2.5, numbers_in("2.37 million"))
    assert not supported(24000, numbers_in("twenty three thousand"))


def test_numbers_on_screen_must_come_from_the_references():
    counter = get_template("number-counter")
    params, _ = validate_params(counter, {"to": 23000, "suffix": " years", "label": "since people walked here"}, None)
    said = {"transcript:S01:10.00-14.00": "people were here twenty three thousand years ago"}
    fc = require_facts(counter, params, said)
    assert fc.ok and fc.numbers[0]["found_in"] == "transcript:S01:10.00-14.00"
    with pytest.raises(RefusedError, match="no source"):
        require_facts(counter, params, {})
    inflated, _ = validate_params(counter, {"to": 30000}, None)
    with pytest.raises(RefusedError, match="not found"):
        require_facts(counter, inflated, said)
    # numbers hidden in text count too
    kt = get_template("kinetic-text")
    p, _ = validate_params(kt, {"text": "99% of tracks are lost"}, None)
    with pytest.raises(RefusedError):
        require_facts(kt, p, said)
    # every chart value is checked; words not in the source are reported, not refused
    chart = get_template("chart")
    p, _ = validate_params(chart, {"data": "2019:12,2020:30", "title": "Tracks per year"}, None)
    doc = {"doc:inputs/stats.md": "In 2019 we logged 12 trackways; in 2020, 30."}
    fc = check_facts(chart, p, doc)
    assert fc.ok and not fc.unsupported_numbers
    p2, _ = validate_params(chart, {"data": "2019:12,2020:31"}, None)
    assert not check_facts(chart, p2, doc).ok
    lt = get_template("lower-third")
    p, _ = validate_params(lt, {"name": "Dan Odess", "title": "Astronaut"}, None)
    fc = check_facts(lt, p, {"transcript:S01:1.00-3.00": "my name is Dan Odess, a paleontologist"})
    assert fc.ok and fc.unsupported_words == ["astronaut"]


# ------------------------------------------------------------------ placement


def test_placement_avoids_the_face_and_the_caption_band():
    lt = get_template("lower-third")
    band = caption_band(STYLE, "clean", 1920, 1080)
    assert band[1] == STYLE["captions"]["safe_zones"]["youtube_long"]["bottom"] and band[0] < band[1] - 0.05
    face_left = [{"x": 0.12, "y": 0.38, "w": 0.16, "h": 0.3}]
    pl = place(lt, 0, 4, faces=lambda a, b: face_left, safe=SAFE, band=band, others=[])
    assert pl["anchor"] == "bottom-right" and pl["face_cover"] == 0
    assert pl["box"][3] <= band[0] - 0.02 + 1e-6                    # above the captions
    assert pl["box"][2] <= SAFE["right"] + 1e-6 and pl["x"] == pytest.approx(pl["box"][2])
    face_right = [{"x": 0.7, "y": 0.38, "w": 0.16, "h": 0.3}]
    assert place(lt, 0, 4, faces=lambda a, b: face_right, safe=SAFE, band=band, others=[])["anchor"] == "bottom-left"
    side = get_template("number-counter")
    pl = place(side, 0, 3, faces=lambda a, b: [{"x": 0.42, "y": 0.2, "w": 0.16, "h": 0.3}], safe=SAFE, band=band, others=[])
    assert pl["face_cover"] == 0 and pl["anchor"].startswith("center-")
    assert (pl["box"][1] + pl["box"][3]) / 2 == pytest.approx(pl["y"])
    # a second graphic avoids the first
    first = place(lt, 0, 4, faces=None, safe=SAFE, band=band, others=[])
    second = place(lt, 0, 4, faces=None, safe=SAFE, band=band, others=[tuple(first["box"])])
    assert second["anchor"] != first["anchor"]
    assert place(get_template("title-card"), 0, 3, faces=None, safe=SAFE, band=band, others=[])["anchor"] == "center-center"
    kt = get_template("kinetic-text")       # captions are hidden meanwhile: low band unless the face is there
    assert place(kt, 0, 2, faces=lambda a, b: [{"x": 0.4, "y": 0.15, "w": 0.2, "h": 0.3}], safe=SAFE, band=band, others=[])["y"] == 0.74
    low = place(kt, 0, 2, faces=lambda a, b: [{"x": 0.4, "y": 0.5, "w": 0.2, "h": 0.3}], safe=SAFE, band=band, others=[])
    assert low["y"] == 0.26 and low["face_cover"] == 0


def test_face_cover_measures_the_whole_head():
    f = {"x": 0.4, "y": 0.3, "w": 0.2, "h": 0.3}
    hb = head_box(f)
    assert hb[1] < f["y"] and hb[3] > f["y"] + f["h"]
    assert face_cover((0.0, 0.0, 1.0, 1.0), [f]) == 1.0
    assert face_cover((0.0, 0.0, 0.2, 0.2), [f]) == 0.0
    assert 0 < face_cover(candidate_box("bottom-left", 0.5, 0.3, SAFE, None, 0.02), [{"x": 0.3, "y": 0.5, "w": 0.2, "h": 0.3}]) < 1


def test_backing_panel_follows_contrast_of_the_current_theme():
    from cutroom.graphics.contrast import luminance, needs_backing, parse_color, ratio, theme_problems

    assert ratio(luminance(parse_color("#FFFFFF")[:3]), luminance(parse_color("#000")[:3])) == pytest.approx(21.0)
    assert parse_color("rgba(12,12,18,0.84)")[3] == 0.84
    bold = load_style("default", client="demo-bold")["graphics"]["theme"]
    bright_sky, dark_room = [0.35, 0.62], [0.01, 0.04]
    assert needs_backing(THEME, bright_sky) and not needs_backing(THEME, dark_room)     # white text
    assert needs_backing(bold, dark_room)                                               # dark text
    assert not needs_backing(THEME, None)                                               # not measured
    assert theme_problems(THEME) == [] and theme_problems(bold) == []
    bad = {"colors": {"text": "#FFFFFF", "surface": "rgba(255,255,255,0.9)", "accent": "#FFFF00"}}
    assert any(p.startswith("text on surface") for p in theme_problems(bad))


# ------------------------------------------------------------------ graphics follow the edit


def _g(gid, start, end, attach_t=None, whole=False):
    return Graphic(id=gid, template="lower-third", params={"name": "A"}, start=start, end=end, whole=whole,
                   attach={"source": "S01", "t": attach_t, "offset": 0.0} if attach_t is not None else None)


def test_graphics_move_with_their_content_and_flag_when_cut():
    tl = make_tl(30.0)
    tl.graphics = [_g("g0001", 12.0, 16.0, 12.0), _g("g0002", 22.0, 24.0, 22.0), _g("g0003", 0, 30, whole=True),
                   _g("g0004", 6.0, 8.0, 6.0)]
    remove_ranges(tl, [Removal("S01", 2.0, 5.0, "manual", "x"), Removal("S01", 21.0, 25.0, "manual", "x"),
                       Removal("S01", 5.6, 6.5, "silence", "x")], tool="t", version=2, min_clip=0.1)
    g1, g2, g3, g4 = tl.graphics
    assert g1.start == pytest.approx(12.0 - 3.0 - 0.9, abs=0.041) and g1.end - g1.start == pytest.approx(4.0)   # moved up (frame grid)
    assert g2.attach.get("detached") and any("g0002" in p for p in timeline_warnings(tl))
    assert not validate_timeline(tl)                       # a lost moment is a warning, never blocks saving
    assert not g4.attach.get("detached") and g4.attach["t"] == pytest.approx(6.5, abs=0.041)   # trimmed pause: next kept moment (frame grid)
    assert g3.start == 0 and g3.end == pytest.approx(tl.duration)
    # moving content later moves its graphic too
    tl2 = make_tl(30.0)
    tl2.graphics = [_g("g0001", 2.0, 4.0, 2.0)]
    move_range(tl2, "S01", 0.0, 10.0, 30.0, "test")
    assert tl2.graphics[0].start == pytest.approx(22.0)
    sync_graphics(tl2)
    assert tl2.graphics[0].start == pytest.approx(22.0)


def test_title_cards_hide_captions_they_overlap():
    cues = [{"from": 0, "to": 30}, {"from": 30, "to": 60}, {"from": 60, "to": 90}, {"from": 90, "to": 120}]
    graphics = [{"from": 25, "to": 65, "hides_captions": True}, {"from": 100, "to": 200, "hides_captions": False}]
    kept, touched = hide_captions_under(cues, graphics)
    assert touched == 3
    assert [(c["from"], c["to"]) for c in kept] == [(0, 25), (65, 90), (90, 120)]
    assert [bool(c.get("trimmed")) for c in kept] == [False, True, False]   # starts late on purpose: not a sync error


# ------------------------------------------------------------------ punch-ins


def test_punch_ins_alternate_on_same_angle_jump_cuts_only():
    from cutroom.tools.graphics_tools import _crop_centre, plan_punch_ins

    tl = make_tl(60.0)
    remove_ranges(tl, [Removal("S01", a, a + 1.0, "silence", "x") for a in (5.0, 12.0, 20.0, 30.0, 30.5 + 1.2)],
                  tool="t", version=2, min_clip=0.1)
    clips = tl.track_clips()   # [0,5] [6,12] [13,20] [21,30] [31,31.7] [32.7,60]
    faces = [{"t": float(t), "faces": [{"x": 0.55, "y": 0.2, "w": 0.15, "h": 0.25}]} for t in range(60)]
    plan = plan_punch_ins(tl, {"S01": faces}, {"S01": [20.5]}, 1.12)
    assert clips[0].id not in plan
    assert clips[1].id in plan                     # jump cut at 5-6 → punch in
    assert clips[2].id not in plan                 # next jump cut → back to 100 %
    assert clips[3].id not in plan                 # source camera change at 20.5: no jump, reset
    assert clips[4].id not in plan                 # 0.7 s clip keeps the framing (no jolt)
    assert clips[5].id in plan
    s, cx, cy = plan[clips[1].id]
    assert s == 1.12 and cx == pytest.approx(1 - 0.5 / 1.12, abs=0.001)   # face at 0.625, crop clamped to the frame
    # the crop keeps the top of the head inside
    cx, cy = _crop_centre([{"t": 0, "faces": [{"x": 0.4, "y": 0.05, "w": 0.2, "h": 0.3}]}], 0, 1, 1.2)
    assert cy == pytest.approx(0.5 / 1.2, abs=1e-3)       # head near the top: crop pinned to the top edge


def test_zoom_filter_expression():
    from cutroom.render.engine import Segment, zoom_filter

    run = [Segment("a", "S01", 0, 50, 0), Segment("b", "S01", 60, 100, 50, (1.12, 0.6, 0.4, 1.12, 0.6, 0.4)),
           Segment("c", "S01", 120, 130, 90, (1.0, 0.5, 0.5, 1.2, 0.5, 0.5))]
    f = zoom_filter(run, 25.0, 960, 540)
    assert "between(in\\,50\\,89)*(0.12000)" in f and "(in-90)/9" in f and "s=960x540" in f
    assert zoom_filter(run[:1], 25.0, 960, 540) is None


# ------------------------------------------------------------------ tool level (no media)


@pytest.fixture
def edit_ctx(jobs_base):
    from cutroom.job import Job
    from cutroom.tools._common import EditContext

    job = Job.create("gfx-test")
    tl = make_tl(40.0)
    text = ("My name is Dan Odess and I am a paleontologist. People were here twenty three thousand years ago. "
            "We found the tracks in every layer. That changes the story.")
    wf = seq(text, start=0.5, word=0.3)
    adir = job.root / "analysis" / "S01"
    adir.mkdir(parents=True)
    (adir / "faces.json").write_text(json.dumps(
        [{"t": float(t), "faces": [{"x": 0.08, "y": 0.36, "w": 0.18, "h": 0.32}]} for t in range(41)]))
    (job.root / "inputs" / "facts.md").write_text("Survey 2024: 61 trackways, 1,500 footprints.", encoding="utf-8")
    return EditContext(job, tl, 1, {"S01": wf}, STYLE, {})


def test_graphic_add_checks_facts_places_and_attaches(edit_ctx):
    from cutroom.tools.graphics_tools import build_graphic

    lt = get_template("lower-third")
    g, warn = build_graphic(edit_ctx, lt, start=1.0, duration=None, variant="pill",
                            params={"name": "Dan Odess", "title": "Paleontologist"}, refs=["sentences:0"],
                            anchor=None, reason="introduce the speaker")
    assert g.placement["anchor"] == "bottom-right"            # the face is on the left
    assert g.facts == [next(iter(g.fact_check["quotes"]))] and "Dan Odess" in next(iter(g.fact_check["quotes"].values()))
    assert g.attach["source"] == "S01" and g.attach["t"] == pytest.approx(1.0)
    assert g.end - g.start >= min_hold(lt, g.params, STYLE["graphics"]["rules"])
    counter = get_template("number-counter")
    g2, _ = build_graphic(edit_ctx, counter, start=4.0, duration=3.0, variant="big",
                          params={"to": 23000, "suffix": " years"}, refs=["sentences:1"], anchor=None, reason="key fact")
    assert g2.fact_check["ok"]
    g3, _ = build_graphic(edit_ctx, counter, start=8.0, duration=3.0, variant="inline",
                          params={"to": 1500, "label": "footprints"}, refs=["doc:inputs/facts.md"], anchor=None, reason="")
    assert g3.facts == ["doc:inputs/facts.md"]
    with pytest.raises(RefusedError):
        build_graphic(edit_ctx, counter, start=8.0, duration=3.0, variant="big", params={"to": 1600},
                      refs=["doc:inputs/facts.md"], anchor=None, reason="")
    with pytest.raises(RefusedError):     # documents must be inside the job
        build_graphic(edit_ctx, counter, start=8.0, duration=3.0, variant="big", params={"to": 1500},
                      refs=["doc:../../etc/passwd"], anchor=None, reason="")
    with pytest.raises(RefusedError, match="too short"):
        build_graphic(edit_ctx, lt, start=1.0, duration=0.5, variant="bar", params={"name": "Dan Odess"},
                      refs=["sentences:0"], anchor=None, reason="")
    edit_ctx.tl.graphics += [g, g2]
    with pytest.raises(RefusedError, match="limit"):
        build_graphic(edit_ctx, lt, start=4.5, duration=2.0, variant="bar", params={"name": "Dan Odess"},
                      refs=["sentences:0"], anchor=None, reason="")


# ------------------------------------------------------------------ Remotion: previews, goldens, theme, render


def _remotion_ready() -> bool:
    from cutroom.graphics.remotion import graphics_dir

    if not os.environ.get("CUTROOM_BROWSER") and Path("/opt/pw-browsers").is_dir():
        found = sorted(Path("/opt/pw-browsers").glob("chromium_headless_shell-*/*/chrome-headless-shell"))
        if found:
            os.environ["CUTROOM_BROWSER"] = str(found[-1])
    return shutil.which("node") is not None and (graphics_dir() / "node_modules" / "@remotion" / "renderer").is_dir()


needs_remotion = [pytest.mark.slow, pytest.mark.skipif(not _remotion_ready(), reason="graphics package not installed")]


def _all_items():
    from cutroom.tools.graphics_tools import sample_params

    return [{"template": s.id, "variant": v, "params": sample_params(s)} for s in load_templates().values() for v in s.variants]


@pytest.mark.slow
@pytest.mark.skipif(not _remotion_ready(), reason="graphics package not installed")
def test_every_template_variant_matches_its_golden_image(tmp_path):
    """Visual regression: each variant at mid-hold, 320x180, against tests/golden/graphics/.
    Regenerate after an intended design change with CUTROOM_UPDATE_GOLDENS=1 (and look at them)."""
    import cv2

    from cutroom.tools.graphics_tools import render_previews

    paths = render_previews(_all_items(), THEME, tmp_path, width=320, height=180)
    update = os.environ.get("CUTROOM_UPDATE_GOLDENS") == "1"
    GOLDEN.mkdir(parents=True, exist_ok=True)
    failures = []
    for p in paths:
        gold = GOLDEN / p.name
        if update or not gold.is_file():
            shutil.copy(p, gold)
            continue
        a, b = cv2.imread(str(p)).astype(np.int16), cv2.imread(str(gold)).astype(np.int16)
        d = np.abs(a - b)
        if d.mean() > 2.0 or (d.max(axis=2) > 48).mean() > 0.01:
            failures.append(f"{p.name}: mean diff {d.mean():.2f}, {(d.max(axis=2) > 48).mean():.2%} pixels differ")
    assert not failures, failures
    assert len(paths) == sum(len(s.variants) for s in load_templates().values())


@pytest.mark.slow
@pytest.mark.skipif(not _remotion_ready(), reason="graphics package not installed")
def test_client_theme_changes_every_graphic_without_code_changes(tmp_path):
    import cv2

    from cutroom.tools.graphics_tools import render_previews, sample_params

    lt = get_template("lower-third")
    item = [{"template": "lower-third", "variant": "bar", "params": sample_params(lt)}]
    bold = load_style("default", client="demo-bold")["graphics"]["theme"]
    a = cv2.imread(str(render_previews(item, THEME, tmp_path / "a", width=640, height=360)[0]))
    b = cv2.imread(str(render_previews(item, bold, tmp_path / "b", width=640, height=360)[0]))

    def share(img, hex_):
        rgb = np.array([int(hex_[i:i + 2], 16) for i in (1, 3, 5)])[::-1]   # BGR
        return float((np.abs(img.astype(int) - rgb).max(axis=2) < 30).mean())

    assert share(a, THEME["colors"]["primary"]) > 0.0005 and share(a, bold["colors"]["primary"]) == 0
    assert share(b, bold["colors"]["primary"]) > 0.0005 and share(b, THEME["colors"]["primary"]) == 0


@pytest.mark.slow
@pytest.mark.skipif(not _remotion_ready(), reason="graphics package not installed")
def test_render_with_graphics_and_punch_in_stays_frame_exact(coded_job_with_words):
    """A punch-in on one clip and a lower third: frames still exact, graphic measured on drawn
    pixels inside the safe area and off the (synthetic) face."""
    from test_render import _frame_lumas

    from cutroom.render.engine import render
    from cutroom.timeline import store
    from cutroom.timeline.model import Keyframe
    from cutroom.timeline.ops import assemble
    from cutroom.tools._common import EditContext
    from cutroom.tools.graphics_tools import build_graphic

    job, wf = coded_job_with_words
    tl = assemble(job)
    remove_ranges(tl, [Removal("S01", 2.0, 3.0, "manual", "test"), Removal("S01", 5.0, 7.0, "manual", "test")],
                  tool="test", version=2, min_clip=0.1)
    tl.track_clips()[1].reframe = [Keyframe(t=0, x=0.5, y=0.5, scale=1.12)]
    ctx = EditContext(job, tl, 1, {"S01": wf}, STYLE, {})
    g, _ = build_graphic(ctx, get_template("lower-third"), start=0.5, duration=2.5, variant="bar",
                         params={"name": "Frame Counter", "title": "Test source"}, refs=None, anchor=None, reason="t")
    tl.graphics.append(g)
    n = store.save_new(job, tl, command="test", summary="graphics test")
    rep = render(job, store.load(job, n), mode="preview", timecode=False)
    assert all(rep["checks"].values()), rep["checks"]
    assert rep["graphics"]["measured"][g.id]["frames_checked"] > 0
    lum = _frame_lumas(job.root / rep["path"])     # centre crop: the graphic sits at the bottom
    src_frames = list(range(0, 50)) + list(range(75, 125)) + list(range(175, 250))
    assert [int(round((v - 16) / 8)) for v in lum] == [n % 25 for n in src_frames]


@pytest.fixture
def coded_job_with_words(jobs_base, tmp_path):
    from test_render import _luma_coded_source

    from cutroom.ingest.ingest import ingest
    from cutroom.ingest.proxy import extract_audio, make_proxy
    from cutroom.job import Job

    src = tmp_path / "coded.mp4"
    _luma_coded_source(src)
    job = Job.create("gfx-render-test")
    ingest(job, [src])
    s = job.manifest.sources[0]
    make_proxy(job, s)
    extract_audio(job, s)
    adir = job.root / "analysis" / "S01"
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "faces.json").write_text(json.dumps(
        [{"t": float(t), "faces": [{"x": 0.42, "y": 0.2, "w": 0.16, "h": 0.28}]} for t in range(11)]))
    return job, seq("one two three four five six.", start=0.5, word=0.5)


def test_stored_references_resolve_again(edit_ctx):
    """graphic-move re-checks facts from the canonical references stored in the timeline."""
    from cutroom.tools.graphics_tools import resolve_refs

    texts, _ = resolve_refs(edit_ctx, ["sentences:1"])
    key = next(iter(texts))
    again, _ = resolve_refs(edit_ctx, [key, "doc:inputs/facts.md"])
    assert again[key] == texts[key] and "twenty three thousand" in again[key].lower()
    assert "doc:inputs/facts.md" in again
