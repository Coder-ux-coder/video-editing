"""Cutting planners on hand-built words (fast), plus the full rough cut on the sample (slow)."""

import numpy as np
import pytest

from cutroom.perception.audio import ENV_HOP
from cutroom.perception.textnorm import normalize
from cutroom.perception.words import EditWord, Untranscribed, WordsFile, refine_words
from cutroom.style import load_style
from cutroom.timeline.planning import detect_retakes, plan_fillers, plan_retakes, plan_silence

ED = load_style()["editing"]


def make_words(spec: list[tuple[str, float, float]], prob: float = 0.95) -> WordsFile:
    """[(text, start, end)] → WordsFile with sentence/clause flags from punctuation."""
    words = []
    for i, (text, a, b) in enumerate(spec):
        words.append(EditWord(i=i, text=text, norm=normalize(text), start=a, end=b, raw_start=a, raw_end=b,
                              prob=prob, segment=0, sentence_end=text.endswith((".", "?", "!")),
                              clause_end=text.endswith(","), filler=normalize(text) in {"um", "uh"}))
    return WordsFile(noise_floor_db=-60, speech_level_db=-20, threshold_db=-45, words=words, untranscribed=[], stats={})


def seq(text: str, start: float = 1.0, word: float = 0.3, gaps: dict | None = None) -> WordsFile:
    """Words of ``text`` back to back; ``gaps`` = {word index: extra pause before it}."""
    out, t = [], start
    for i, w in enumerate(text.split()):
        t += (gaps or {}).get(i, 0.0)
        out.append((w, round(t, 3), round(t + word, 3)))
        t += word
    return make_words(out)


def kept(wf: WordsFile):
    return [(0.0, wf.words[-1].end + 2.0)]


# ------------------------------------------------------------------ silence

def test_long_pause_shortened_to_style_length():
    # The next sentence is long, so this is an ordinary pause (not a possible dramatic one).
    wf = seq("I went home. Then I slept for a very long time.", gaps={3: 1.5})
    rs = plan_silence("S01", wf, kept(wf), ED)
    mid = [r for r in rs if r.kind == "silence"]
    assert len(mid) == 1
    r = mid[0]
    home, then = wf.words[2], wf.words[3]
    assert home.end < r.src_in and r.src_out < then.start           # cut only inside the pause
    kept_pause = (r.src_in - home.end) + (then.start - r.src_out)
    assert kept_pause == pytest.approx(ED["keep_pause_sentence_end"], abs=0.02)


def test_short_pauses_untouched_and_head_tail_trimmed():
    wf = seq("one two three four", start=2.0, gaps={2: 0.3})
    rs = plan_silence("S01", wf, kept(wf), ED)
    assert {r.kind for r in rs} == {"head", "tail"}
    head = next(r for r in rs if r.kind == "head")
    assert head.src_in == 0.0 and wf.words[0].start - head.src_out == pytest.approx(ED["head_pad"], abs=0.01)


def test_dramatic_pause_is_kept_longer():
    wf = seq("We tested everything for months. It failed.", gaps={5: 1.5})
    r = next(r for r in plan_silence("S01", wf, kept(wf), ED) if r.kind == "silence")
    assert "dramatic" in r.reason and r.confidence < 0.9
    kept_pause = (r.src_in - wf.words[4].end) + (wf.words[5].start - r.src_out)
    assert kept_pause >= ED["dramatic_pause_keep"] - 0.02


def test_cut_never_splits_a_breath():
    wf = seq("I went home. Then I slept.", gaps={3: 1.5})
    breath = Untranscribed(start=wf.words[2].end + 0.05, end=wf.words[2].end + 0.4, kind="unvoiced", voicing=0.1,
                           level_db=-40, after_word=2)
    wf.untranscribed = [breath]
    r = next(r for r in plan_silence("S01", wf, kept(wf), ED) if r.kind == "silence")
    assert not (breath.start < r.src_in < breath.end) and not (breath.start < r.src_out < breath.end)


# ------------------------------------------------------------------ fillers

def test_um_removed_with_natural_pause_and_covering_the_word():
    wf = seq("So um I think so.", gaps={1: 0.2, 2: 0.2})
    rs = plan_fillers("S01", wf, kept(wf), ED)
    assert len(rs) == 1 and rs[0].kind == "filler"
    um = wf.words[1]
    assert rs[0].src_in <= um.start and rs[0].src_out >= um.end


def test_um_touching_neighbours_is_still_fully_removed():
    wf = seq("So um I think so.")          # no pauses at all
    r = plan_fillers("S01", wf, kept(wf), ED)[0]
    assert r.src_in <= wf.words[1].start + 1e-6 and r.src_out >= wf.words[1].end - 1e-6


def test_contextual_fillers_only_when_enabled_and_set_off_by_commas():
    wf = seq("It was, like, huge. I like pizza.")
    assert plan_fillers("S01", wf, kept(wf), ED) == []
    rs = plan_fillers("S01", wf, kept(wf), ED | {"remove_contextual_fillers": True})
    assert [r.text for r in rs] == ["like,"]       # "I like pizza" is protected


def test_acoustic_filler_in_pause_removed():
    wf = seq("I think so.", gaps={1: 0.8})
    s = Untranscribed(start=wf.words[0].end + 0.2, end=wf.words[0].end + 0.6, kind="voiced", voicing=0.8,
                      level_db=-25, after_word=0)
    wf.untranscribed = [s]
    rs = plan_fillers("S01", wf, kept(wf), ED)
    assert len(rs) == 1 and rs[0].kind == "acoustic_filler"
    assert rs[0].src_in <= s.start and rs[0].src_out >= s.end


# ------------------------------------------------------------------ retakes

def drops(wf, **kw):
    return {" ".join(w.text for w in c.drop): c for c in detect_retakes(wf, kept(wf), ED | kw)}


def test_stumble_detected():
    d = drops(seq("Today we are, we are going to bake bread."))
    assert "we are," in d and d["we are,"].kind == "stumble" and d["we are,"].confidence >= 0.75


def test_single_word_stumble_detected_but_not_intentional_repeats():
    d = drops(seq("We need the the data. It is very very good. Oh, oh, wow."))
    assert set(d) == {"the"}


def test_sentence_retake_keeps_the_later_take():
    wf = seq("Then let the dough rest for two hours. Then let the dough rest for two hours. Bake it.",
             gaps={8: 2.0})
    d = drops(wf)
    c = next(iter(d.values()))
    assert c.kind == "retake" and c.confidence >= 0.75 and c.keep_from.i == 8


@pytest.mark.parametrize("text", [
    "we need to know how to document it and how to preserve it.",
    "up until 10,000 years ago or 5,000 years ago or today?",
    "this is important and this is interesting because of that.",
    "in a way that's really neat. In a way that you can't do with stone tools.",
])
def test_parallel_structure_is_not_a_retake(text):
    assert drops(seq(text)) == {}


def test_parallel_sentences_with_shared_opening_are_not_retakes():
    wf = seq("We want to know when people first got here. We want to know what the climate was like.", gaps={9: 1.0})
    assert drops(wf) == {}


def test_spelled_name_is_not_a_stumble():
    wf = make_words([("O-D-E-S-S.", 1.0, 2.0), ("And", 2.5, 2.7), ("hi.", 2.7, 3.0)])
    assert drops(wf) == {}


def test_correction_keeps_the_added_word():
    wf = seq("when they encounter human tracks, fresh human tracks.")
    d = drops(wf)
    assert list(d) == ["human tracks,"]     # "fresh" stays: it is part of the correction


def test_number_correction_needs_a_person():
    wf = seq("pushing them back 10,000 years, 8,000 years further into the past.")
    c = next(iter(drops(wf).values()), None)
    assert c is None or c.confidence < 0.75


def test_retake_removal_covers_the_abandoned_words():
    wf = seq("Today we are, we are going home.")
    cands = detect_retakes(wf, kept(wf), ED)
    r = plan_retakes("S01", wf, kept(wf), ED, cands)[0]
    assert r.src_in <= wf.words[1].start + 1e-6 and r.src_out >= wf.words[2].end - 1e-6
    assert r.src_out <= wf.words[3].start + 1e-6       # the kept take starts intact


# ------------------------------------------------------------------ word-boundary refinement

def test_refinement_snaps_edges_to_real_pauses():
    """Synthetic envelope: speech in [1.0,2.0] and [3.0,4.0]; Whisper's raw edges are off."""
    dur = 5.0
    env = np.full(int(dur / ENV_HOP), -60.0, np.float32)
    for a, b in ((1.0, 2.0), (3.0, 4.0)):
        env[int(a / ENV_HOP): int(b / ENV_HOP)] = -20.0
    raw = [{"text": "hello", "start": 0.6, "end": 1.8, "prob": 0.9, "segment": 0},     # starts 400 ms early
           {"text": "there.", "start": 3.25, "end": 3.7, "prob": 0.9, "segment": 0}]   # starts late, ends early
    words, stats = refine_words(raw, env, -45.0, dur)
    assert words[0]["start"] == pytest.approx(1.0, abs=0.02)
    assert words[0]["end"] == pytest.approx(2.0, abs=0.02)
    assert words[1]["start"] == pytest.approx(3.0, abs=0.02)
    assert words[1]["end"] == pytest.approx(4.0, abs=0.02)
    assert stats["edges_refined"] == 4


def test_refinement_leaves_continuous_speech_alone():
    dur = 3.0
    env = np.full(int(dur / ENV_HOP), -20.0, np.float32)
    raw = [{"text": "a", "start": 1.0, "end": 1.3, "prob": 0.9, "segment": 0},
           {"text": "b", "start": 1.3, "end": 1.6, "prob": 0.9, "segment": 0}]
    words, _ = refine_words(raw, env, -45.0, dur)
    assert words[0]["end"] == 1.3 and words[1]["start"] == 1.3


def test_sentence_cut_and_block_leave_natural_pauses():
    from cutroom.timeline.planning import plan_sentence_cut, sentence_block

    wf = seq("Intro line here. Hook line here. Body line here.", gaps={3: 0.6, 6: 0.6})
    r = plan_sentence_cut("S01", wf, kept(wf), ED, 1, 1, "test")
    hook = wf.words[3:6]
    assert r.src_in <= hook[0].start and r.src_out >= hook[-1].end
    pause = (r.src_in - wf.words[2].end) + (wf.words[6].start - r.src_out)
    assert pause == pytest.approx(ED["keep_pause_sentence_end"], abs=0.02)
    a, b = sentence_block(wf, kept(wf), 1, 1)
    assert wf.words[2].end < a < hook[0].start and hook[-1].end < b < wf.words[6].start


def test_move_range_reorders_without_losing_material():
    from test_timeline import make_tl

    from cutroom.timeline.ops import move_range

    tl = make_tl(20.0)
    moved, at = move_range(tl, "S01", 8.0, 10.0, 0.0, "cold open")
    order = [(c.src_in, c.src_out) for c in tl.track_clips()]
    assert order == [(8.0, 10.0), (0.0, 8.0), (10.0, 20.0)] and at == 0.0
    assert tl.duration == pytest.approx(20.0) and "moved: cold open" in moved[0].reason


def test_cut_moves_onto_nearby_camera_change():
    wf = seq("I went home. Then I slept for a very long time.", gaps={3: 1.5})
    home, then = wf.words[2], wf.words[3]
    plain = next(r for r in plan_silence("S01", wf, kept(wf), ED) if r.kind == "silence")
    shot = plain.src_out + 0.05           # camera change in the kept pause, just before the pad
    snapped = next(r for r in plan_silence("S01", wf, kept(wf), ED, shots=[shot]) if r.kind == "silence")
    assert snapped.src_out == pytest.approx(shot) and snapped.src_out <= then.start - 0.02
    far = next(r for r in plan_silence("S01", wf, kept(wf), ED, shots=[plain.src_out + 2.0]) if r.kind == "silence")
    assert far.src_out == plain.src_out   # too far: untouched


def test_refinement_finds_a_pause_whisper_did_not_report():
    """Whisper says the words touch (raw gap 0), but the audio has a 0.3 s pause between them."""
    dur = 4.0
    env = np.full(int(dur / ENV_HOP), -60.0, np.float32)
    for a, b in ((1.0, 2.0), (2.3, 3.0)):
        env[int(a / ENV_HOP): int(b / ENV_HOP)] = -20.0
    raw = [{"text": "Sands", "start": 1.0, "end": 2.05, "prob": 0.9, "segment": 0},
           {"text": "during", "start": 2.05, "end": 3.0, "prob": 0.9, "segment": 0}]
    words, _ = refine_words(raw, env, -45.0, dur)
    assert words[0]["end"] == pytest.approx(2.0, abs=0.02)
    assert words[1]["start"] == pytest.approx(2.3, abs=0.02)
