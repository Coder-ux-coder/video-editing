"""Speaker detection: per-sentence assignment, telemetry kept off, cutting a speaker, Shorts and
review using it; and (slow, needs the downloaded model) a two-voice dialogue with known answers."""

import json
import os
import subprocess
import sys

import numpy as np
import pytest

from conftest import needs_ffmpeg
from test_delivery import cut_job  # noqa: F401  (fixture)

DIALOGUE = [("slt", "Welcome back to the show. Today we talk about bread."),
            ("rms", "Thanks for having me. I have been baking for twenty years."),
            ("slt", "What is the most common mistake people make?"),
            ("rms", "They rush the rise. The dough needs time to rest, at least two hours."),
            ("slt", "And what about the oven temperature?"),
            ("rms", "Hot. Two hundred and twenty degrees, and a tray of water for steam."),
            ("slt", "Thank you so much for joining us today.")]


def test_sentences_get_the_majority_speaker_and_word_blips_do_not_split_them():
    from cutroom.perception.speakers import assign_sentences, summarize

    sents = [{"n": 0, "start": 0.0, "end": 3.0, "text": "Hello there."},
             {"n": 1, "start": 3.5, "end": 6.0, "text": "And what is your title?"},
             {"n": 2, "start": 6.5, "end": 12.0, "text": "Well, I am the chief scientist here."}]
    turns = [(0.0, 3.1, "A"), (3.4, 6.1, "B"), (6.5, 6.8, "B"), (6.8, 12.0, "A")]   # "Well," blip by B
    got = assign_sentences(sents, turns)
    assert [g["speaker"] for g in got] == ["A", "B", "A"]
    assert got[2]["confidence"] == pytest.approx(5.2 / 5.5, abs=0.01)
    summ = summarize(got)
    assert summ[0]["id"] == "A" and summ[0]["sentences"] == 2 and summ[1]["share"] == pytest.approx(2.5 / 11.0, abs=0.01)


def test_pyannote_telemetry_is_off():
    pytest.importorskip("pyannote.audio")
    import cutroom.perception.speakers  # noqa: F401  (sets the switch before pyannote loads)
    from pyannote.audio.telemetry.metrics import is_metrics_enabled

    assert os.environ["PYANNOTE_METRICS_ENABLED"] in ("0", "false") and not is_metrics_enabled()


@pytest.fixture
def two_voice_job(cut_job):
    """The delivery test job with sentence 2 (fully in the edit) said by an interviewer (speakers.json written as the
    `speakers` command would)."""
    from cutroom.perception.topics import sentences
    from cutroom.timeline.ops import words_for

    from cutroom.perception.pipeline import WORDS_VERSION

    job = cut_job
    wp = job.root / "analysis" / "S01" / "words.json"
    wd = json.loads(wp.read_text())
    wd.setdefault("stats", {})["version"] = WORDS_VERSION     # edit-ready as is: no re-transcription
    wp.write_text(json.dumps(wd))
    sents = sentences(words_for(job, "S01"))
    doc = {"schema": "cutroom.speakers/1", "source": "S01", "main": "SPEAKER_00", "names": {},
           "speakers": [{"id": "SPEAKER_00", "seconds": 5.0, "share": 0.7, "sentences": 2},
                        {"id": "SPEAKER_01", "seconds": 2.0, "share": 0.3, "sentences": 1}],
           "turns": [], "sentences": [{**s, "speaker": "SPEAKER_01" if s["n"] == 2 else "SPEAKER_00", "confidence": 1.0}
                                      for s in sents]}
    (job.root / "analysis" / "S01" / "speakers.json").write_text(json.dumps(doc))
    return job


def _cutroom(*args):
    return subprocess.run([sys.executable, "-m", "cutroom", *args], capture_output=True, text=True, env=os.environ.copy())


def test_naming_and_cutting_a_speaker_and_the_review_check(two_voice_job):
    from cutroom.perception import speakers as SP
    from cutroom.review.checks import ReviewInput, check_speakers
    from cutroom.style import load_style
    from cutroom.timeline import store
    from cutroom.timeline.ops import words_for
    from cutroom.timeline.outline import outline

    job = two_voice_job
    SP.set_names(job, "S01", {"SPEAKER_01": "Interviewer"})
    tl = store.load(job)

    def other_voices(tl):
        ri = ReviewInput(tl=tl, words={"S01": words_for(job, "S01")}, style=load_style(), job_root=job.root)
        return check_speakers(ri)

    found = other_voices(tl)
    assert [i.data["sentence"] for i in found] == [2] and "Interviewer" in found[0].evidence
    assert "another voice: Interviewer says sentence 2" in outline(tl, {"S01": words_for(job, "S01")},
                                                                    speakers={"S01": SP.load(job, "S01")})
    refused = _cutroom("cut", job.id, "--speaker", "SPEAKER_00", "--reason", "x")
    assert refused.returncode != 0 and "main speaker" in refused.stdout + refused.stderr
    done = _cutroom("cut", job.id, "--speaker", "Interviewer", "--reason", "interviewer question")
    assert done.returncode == 0, done.stdout + done.stderr
    after = store.load(job)
    assert after.meta.version == tl.meta.version + 1 and other_voices(after) == []
    assert _cutroom("cut", job.id, "--speaker", "Interviewer", "--reason", "again").returncode != 0   # nothing left


def test_shorts_windows_with_another_voice_are_marked_down():
    from cutroom.shorts.find import TSentence, score_window

    def s(i, text, other=None):
        return TSentence(text=text, start=i * 10.0, end=i * 10.0 + 9, words=20, source="S01", src_start=i * 10.0,
                         src_end=i * 10.0 + 9, index=i, other_speaker=other)

    solo = score_window([s(0, "Here is the finding."), s(1, "It changes everything.")], [], {})
    mixed = score_window([s(0, "Here is the finding."), s(1, "What do you mean?", other="SPEAKER_01")], [], {})
    assert "speakers" not in solo.parts and mixed.parts["speakers"] == -2.0
    assert any("another voice" in r and "sentence 1" in r for r in mixed.reasons)


@needs_ffmpeg
@pytest.mark.slow
def test_two_voice_dialogue_is_split_by_speaker(tmp_path):
    """Seven alternating sentences by two synthetic voices (FFmpeg's flite), known boundaries:
    every sentence must get its own voice's label."""
    pytest.importorskip("pyannote.audio")
    import soundfile as sf

    from cutroom.perception import speakers as SP

    if not (SP.model_path() / "config.yaml").is_file():
        pytest.skip("speaker model not downloaded (gated; see CHARTER 4.11)")
    sr, parts, truth, t = 16000, [], [], 0.0
    for voice, text in DIALOGUE:
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"flite=text='{text}':voice={voice}",
                            "-ar", str(sr), "-ac", "1", "-f", "f32le", "-"], capture_output=True)
        if r.returncode != 0:
            pytest.skip("FFmpeg without flite")
        a = np.frombuffer(r.stdout, np.float32)
        nz = np.nonzero(np.abs(a) > 0.01)[0]
        a = a[nz[0]:nz[-1] + 1]
        truth.append({"n": len(truth), "voice": voice, "start": t, "end": t + len(a) / sr, "text": text})
        parts += [a, np.zeros(int(0.6 * sr), np.float32)]
        t += len(a) / sr + 0.6
    wav = tmp_path / "dialogue.wav"
    sf.write(str(wav), np.concatenate(parts), sr)
    got = SP.assign_sentences(truth, SP.turns_for(wav))
    mapping = {}
    for g, x in zip(got, truth):
        mapping.setdefault(x["voice"], g["speaker"])
        assert g["speaker"] == mapping[x["voice"]], f"sentence {x['n']} ({x['voice']}) labelled {g['speaker']}"
    assert len(set(mapping.values())) == 2 and min(g["confidence"] for g in got) > 0.9
