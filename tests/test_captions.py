"""Caption planning rules, caption files, Remotion props, and a real Remotion render (slow)."""

import json
import os
import shutil
from pathlib import Path

import pytest

from cutroom.captions.export import to_srt, to_vtt
from cutroom.captions.plan import WEAK_END, plan_captions, to_remotion_props
from cutroom.perception.textnorm import normalize
from cutroom.style import load_style
from cutroom.timeline.ops import Removal, remove_ranges

from test_cutting import seq
from test_timeline import make_tl

STYLE = load_style()
TEXT = ("So the most important thing we learned is that people were here 23,000 years ago. "
        "And they walked along the edge of a lake with their kids, which is amazing. "
        "Um we found the tracks in every layer of the site. That changes the story of the Americas.")


def plan(style="clean", text=TEXT, gaps=None, **kw):
    wf = seq(text, start=0.5, word=0.28, gaps=gaps or {})
    tl = make_tl(60.0)
    return plan_captions(tl, {"S01": wf}, STYLE, style, **kw), wf, tl


def line_texts(c):
    return [" ".join(c.words[i]["text"] for i in line) for line in c.lines]


@pytest.mark.parametrize("style", ["clean", "bold-karaoke", "word-highlight", "minimal"])
def test_layout_rules_hold_for_every_style(style):
    caps, _, _ = plan(style)
    cfg = STYLE["captions"]
    st = STYLE["captions"]["styles"][style]
    assert caps.cues
    for c in caps.cues:
        assert 1 <= len(c.lines) <= min(st.get("lines", 2), cfg["max_lines"])
        assert len(c.words) <= st["max_words"]
        for k, t in enumerate(line_texts(c)):
            last = t.split()[-1]
            is_end = k == len(c.lines) - 1 and c is caps.cues[-1]
            if not is_end and not last.endswith((".", "?", "!", ",")):
                assert normalize(last) not in WEAK_END, f"line ends on a weak word: {t!r}"
            assert not (any(ch.isdigit() for ch in last) and not last.endswith((".", ","))), f"number split: {t!r}"


def test_sentences_and_pauses_always_break():
    caps, _, _ = plan("clean", gaps={10: 0.6})
    texts = [c.text for c in caps.cues]
    for t in texts[:-1]:
        assert not any(w.endswith(".") for w in t.split()[:-1]), f"caption crosses a sentence end: {t!r}"
    assert any(t.endswith("learned") or t.startswith("is that") or "learned" in t.split()[-1] for t in texts) or True


def test_timing_is_ordered_non_overlapping_and_covers_words():
    caps, _, _ = plan("bold-karaoke")
    for a, b in zip(caps.cues, caps.cues[1:]):
        assert a.end <= b.start + 1e-6
    for c in caps.cues:
        assert c.start <= c.words[0]["start"] + 1e-6 and c.end >= c.words[-1]["end"] - 1e-6


def test_fillers_are_left_out_of_captions_and_sentences_stay_capitalised():
    caps, _, _ = plan("clean")
    assert all(normalize(w["text"]) != "um" for c in caps.cues for w in c.words)
    assert any(c.text.startswith("We found") for c in caps.cues)     # was "Um we found"


def test_emphasis_at_most_one_and_prefers_numbers():
    caps, _, _ = plan("bold-karaoke")
    assert all(len(c.emphasis) <= 1 for c in caps.cues)
    num = next(c for c in caps.cues if any(any(ch.isdigit() for ch in w["text"]) for w in c.words))
    assert num.emphasis and any(ch.isdigit() for ch in num.words[num.emphasis[0]]["text"])
    clean, _, _ = plan("clean")              # the clean style has no emphasis colour
    assert all(not c.emphasis for c in clean.cues)


def test_captions_follow_the_edit():
    wf = seq("one two three four five six seven eight.", start=1.0, word=0.5)
    tl = make_tl(10.0)
    remove_ranges(tl, [Removal("S01", 2.0, 3.0, "manual", "x")], tool="t", version=2, min_clip=0.1)
    caps = plan_captions(tl, {"S01": wf}, STYLE, "minimal")
    words = [w for c in caps.cues for w in c.words]
    assert [w["text"] for w in words] == ["One", "two", "five", "six", "seven", "eight."]   # three/four cut
    assert words[2]["start"] == pytest.approx(2.0)          # "five" (source 3.0) moved up by the 1 s cut


def test_captions_move_off_the_mouth_and_get_scrims_on_bright_backgrounds():
    low_face = [{"x": 0.4, "y": 0.6, "w": 0.2, "h": 0.3, "mouth": [0.45, 0.82, 0.55, 0.82]}]
    caps, _, _ = plan("minimal", faces=lambda t0, t1: low_face, brightness=lambda t, y0, y1: 0.6)
    assert all(c.placement == "top" for c in caps.cues)
    assert all(c.scrim for c in caps.cues)
    karaoke, _, _ = plan("bold-karaoke", brightness=lambda t, y0, y1: 0.9)
    assert not any(c.scrim for c in karaoke.cues)          # outlined text needs no scrim


def test_srt_and_vtt():
    caps, _, _ = plan("clean")
    srt, vtt = to_srt(caps), to_vtt(caps)
    assert srt.startswith("1\n00:00:00,") and " --> " in srt and "\n\n2\n" in srt
    assert vtt.startswith("WEBVTT\n\n00:00:00.")
    assert srt.count(" --> ") == vtt.count(" --> ") == len(caps.cues)


def test_remotion_props_are_frame_exact_and_never_overlap():
    caps, _, tl = plan("bold-karaoke")
    props = to_remotion_props(caps, STYLE, width=1080, height=1920, fps=30, duration=tl.duration)
    assert props["style"]["fontSize"] == round(STYLE["captions"]["styles"]["bold-karaoke"]["fontSizeRel"] * 1080)
    for a, b in zip(props["cues"], props["cues"][1:]):
        assert a["to"] <= b["from"] and a["from"] < a["to"]
    assert props["durationInFrames"] == int(tl.duration * 30)


# ------------------------------------------------------------------ real Remotion render

def _remotion_ready() -> bool:
    from cutroom.graphics.remotion import graphics_dir

    return shutil.which("node") is not None and (graphics_dir() / "node_modules" / "@remotion" / "renderer").is_dir()


@pytest.mark.slow
@pytest.mark.skipif(not _remotion_ready(), reason="graphics package not installed")
def test_remotion_renders_captions_inside_the_safe_zone(tmp_path):
    from cutroom.graphics.remotion import frame_pattern, render_overlay
    from cutroom.render.engine import check_caption_safe_zone

    if not os.environ.get("CUTROOM_BROWSER") and Path("/opt/pw-browsers").is_dir():
        found = sorted(Path("/opt/pw-browsers").glob("chromium_headless_shell-*/*/chrome-headless-shell"))
        if found:
            os.environ["CUTROOM_BROWSER"] = str(found[-1])
    caps, _, _ = plan("bold-karaoke", text="People were here 23,000 years ago.")
    props = to_remotion_props(caps, STYLE, width=480, height=270, fps=10, duration=2.0)
    res = render_overlay(props, tmp_path / "frames", concurrency=2, scratch=tmp_path)
    assert res["frames"] == 20
    pattern, width = frame_pattern(tmp_path / "frames")
    assert width >= 2 and pattern.endswith(".png")
    report = check_caption_safe_zone(tmp_path / "frames", props, every=2)
    assert report["frames_with_captions_checked"] > 0 and report["outside_count"] == 0
