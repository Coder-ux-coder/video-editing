"""Phase 6: licence-tracked library, generated music and effects, ducked mixing, b-roll
(suggestions, sync, frame-exact render), thumbnails checks, reference styles."""

import json

import numpy as np
import pytest
import soundfile as sf

from cutroom.assets import library as L
from cutroom.errors import RefusedError


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = tmp_path / "library"
    monkeypatch.setattr(L, "library_dir", lambda: root)
    return root


def test_licence_allowlist():
    assert L.normalize_license("Public domain") == ("public-domain", False)
    assert L.normalize_license("CC0") == ("CC0-1.0", False)
    assert L.normalize_license("CC BY 4.0") == ("CC-BY-4.0", True)
    assert L.normalize_license("PD-USGov-Interior-NPS")[0] == "public-domain"
    for bad in ("CC BY-SA 4.0", "CC BY-NC 2.0", "CC BY-ND 4.0", "All rights reserved", ""):
        with pytest.raises(RefusedError):
            L.normalize_license(bad)


def test_library_records_licence_and_deduplicates(lib, tmp_path):
    import cv2

    img = tmp_path / "tracks.png"
    cv2.imwrite(str(img), np.full((1080, 1920, 3), 120, np.uint8))
    with pytest.raises(RefusedError, match="attribution"):
        L.add_file(img, kind="image", license="CC BY 4.0")
    a = L.add_file(img, kind="image", license="CC BY 4.0", author="A. Person", title="Tracks", tags=["Tracks"])
    side = json.loads((lib / a.file).with_suffix(".json").read_text())
    assert side["license"] == "CC-BY-4.0" and side["attribution_required"] and side["width"] == 1920
    assert "A. Person" in a.credit and "CC-BY-4.0" in a.credit and a.tags == ["tracks"]
    assert L.add_file(img, kind="image", license="CC0").id == a.id          # same bytes: same asset
    with pytest.raises(RefusedError):
        L.add_file(img, kind="music", license="CC0")                         # wrong kind for the file


def test_generated_music_loops_seamlessly_and_effects_exist():
    from cutroom.assets.synth import MOODS, SFX_KINDS, SR, music_bed, sfx

    for mood in MOODS:
        y = music_bed(mood, 20.0)
        assert y.shape[1] == 2 and np.abs(y).max() <= 0.5 + 1e-6
        k = int(MOODS[mood]["bar_s"] * len(MOODS[mood]["chords"]) * SR)
        if k < len(y) - 3:
            d = np.abs(np.diff(y[:, 0]))
            assert d[k - 3:k + 3].max() < 5 * np.percentile(d, 99), mood      # no click at the loop point
    for kind in SFX_KINDS:
        s = sfx(kind)
        assert 0.05 < len(s) / SR < 2.0 and np.abs(s).max() > 0.5


def test_music_ducks_under_speech_with_look_ahead(tmp_path):
    from cutroom.assets.synth import SR, music_bed, write
    from cutroom.audio.mix import _duck_curve, mix_beds

    n = 12 * SR
    t = np.arange(n) / SR
    voice = np.zeros(n, np.float32)
    spans = [(1.0, 4.0), (7.0, 10.0)]                     # "speech": tones in two blocks, a 3 s pause
    for a, b in spans:
        voice[int(a * SR):int(b * SR)] = 0.1 * np.sin(2 * np.pi * 220 * t[int(a * SR):int(b * SR)])
    vw = tmp_path / "voice.wav"
    sf.write(str(vw), voice, SR, subtype="FLOAT")
    bed = write(tmp_path / "bed.wav", music_bed("calm", 12.0))
    st = mix_beds(vw, tmp_path / "mix.wav", [{"kind": "music", "path": str(bed), "start": 0, "end": 12, "duck": True}],
                  spans, cfg={"music_under_speech_db": 12, "duck_db": 8}, scratch=tmp_path)
    assert st["speech_to_music_db"] == pytest.approx(20, abs=2.5)          # 12 + 8 dB under speech
    assert st["music_in_pauses_db_under_speech"] < st["speech_to_music_db"] - 2   # rises in the pause
    g = _duck_curve(np.repeat([False, True], SR), depth_db=8)
    assert g[int(0.9 * SR)] < 0.8                                           # already dipping before speech


def test_broll_suggestions_and_edits(tmp_path):
    from test_cutting import seq
    from test_timeline import make_tl

    from cutroom.timeline.model import BRoll
    from cutroom.timeline.ops import Removal, remove_ranges
    from cutroom.tools.media_tools import suggest_broll

    tl = make_tl(30.0)
    wf = seq("We found mammoth tracks in the mud. Then we went home.", start=1.0, word=0.4)
    a = L.Asset(id="image-mammoth", kind="image", file="x.jpg", license="public-domain", attribution_required=False,
                title="Mammoth tracks at White Sands", tags=["mammoth", "tracks"])
    other = L.Asset(id="image-car", kind="image", file="y.jpg", license="public-domain", attribution_required=False,
                    title="A car", tags=["car"])
    sug = suggest_broll(tl, {"S01": wf}, [a, other])
    assert [s["asset"] for s in sug] == ["image-mammoth"] and set(sug[0]["matches"]) >= {"mammoth", "tracks"}
    tl.broll = [BRoll(id="b0001", asset="image-mammoth", kind="image", start=10.0, end=13.0,
                      attach={"source": "S01", "t": 10.0, "offset": 0.0})]
    remove_ranges(tl, [Removal("S01", 2.0, 4.0, "manual", "x")], tool="t", version=2, min_clip=0.1)
    assert tl.broll[0].start == pytest.approx(8.0, abs=0.05)                # moves with its content


def test_reference_style_derivation():
    from cutroom.reference import derive_style

    m = {"shots": 40, "shot_s": {"median": 4.0, "p25": 2.5, "p75": 6.0, "p90": 9.0},
         "pause_mid_s": {"median": 0.15, "p90": 0.4}, "pause_sentence_s": {"median": 0.45, "p90": 0.8}}
    d = derive_style(m)
    assert d["editing"] == {"keep_pause_mid_sentence": 0.15, "keep_pause_sentence_end": 0.45, "max_pause": 0.8}
    assert d["pacing"] == {"visual_change_target_s": [2.5, 6.0], "max_static_s": 9.0}
    one_shot = derive_style({**m, "shots": 1, "pause_sentence_s": {"median": 2.0, "p90": 3.0}})
    assert one_shot["editing"]["keep_pause_sentence_end"] == 0.6 and one_shot["editing"]["max_pause"] == 1.0
    assert one_shot["pacing"] == {}                                        # too few shots to learn pacing


def test_thumbnail_checks(tmp_path):
    import cv2

    from cutroom.style import load_style
    from cutroom.thumbnails import check_thumbnail

    theme = load_style()["graphics"]["theme"]
    mask = np.zeros((720, 1280), np.uint8)
    cv2.putText(mask, "TITLE", (60, 360), cv2.FONT_HERSHEY_SIMPLEX, 4, 255, 12)
    glyph = mask > 0
    p = tmp_path / "t.jpg"
    dark = np.full((720, 1280, 3), 30, np.uint8)
    img = dark.copy()
    img[glyph] = 255
    cv2.imwrite(str(p), img)
    ok = check_thumbnail(p, img, dark, glyph, (0.6, 0.2, 0.8, 0.6), theme, accent=False)
    assert all(ok["checks"].values()) and ok["contrast"] > 3
    on_face = check_thumbnail(p, img, dark, glyph, (0.0, 0.3, 0.4, 0.7), theme, accent=False)
    assert not on_face["checks"]["face_clear_of_text"]
    bright = np.full((720, 1280, 3), 235, np.uint8)       # white title on a near-white background
    img2 = bright.copy()
    img2[glyph] = 255
    assert not check_thumbnail(p, img2, bright, glyph, (0.6, 0.2, 0.8, 0.6), theme, accent=False)["checks"]["text_contrast"]


@pytest.mark.slow
def test_broll_render_is_frame_exact(lib, tmp_path, jobs_base):
    """A cutaway still replaces exactly its frames; the frames around it are the source's."""
    import cv2
    from test_render import _frame_lumas, _luma_coded_source

    from cutroom.ingest.ingest import ingest
    from cutroom.ingest.proxy import extract_audio, make_proxy
    from cutroom.job import Job
    from cutroom.render.engine import render
    from cutroom.timeline import store
    from cutroom.timeline.model import BRoll
    from cutroom.timeline.ops import assemble

    src = tmp_path / "coded.mp4"
    _luma_coded_source(src)
    job = Job.create("broll-test")
    ingest(job, [src])
    s = job.manifest.sources[0]
    make_proxy(job, s)
    extract_audio(job, s)
    img = tmp_path / "still.png"
    cv2.imwrite(str(img), np.full((1080, 1920, 3), 250, np.uint8))
    a = L.add_file(img, kind="image", license="CC0", title="white")
    tl = assemble(job)
    tl.broll = [BRoll(id="b0001", asset=a.id, kind="image", start=2.0, end=3.0)]
    n = store.save_new(job, tl, command="t", summary="t")
    rep = render(job, store.load(job, n), mode="preview", timecode=False, captions=False)
    lum = _frame_lumas(job.root / rep["path"])
    assert len(lum) == 250 and rep["checks"]["frames_ok"]
    assert all(v > 230 for v in lum[50:75])                                  # frames 50-74: the still
    assert [int(round((v - 16) / 8)) for v in lum[:50]] == [k % 25 for k in range(50)]
    assert [int(round((v - 16) / 8)) for v in lum[75:]] == [k % 25 for k in range(75, 250)]
