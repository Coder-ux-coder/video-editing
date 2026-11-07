"""Real transcription tests. Marked slow: they need a downloaded model."""

import pytest

from cutroom.doctor import boundary_errors
from cutroom.ingest.proxy import extract_audio
from cutroom.perception.models import is_downloaded
from cutroom.perception.textnorm import word_error_rate
from cutroom.perception.transcribe import load_transcript, readable_text, save_transcript, to_srt, transcribe_audio
from cutroom.system.profile import ASRSettings

from conftest import needs_ffmpeg

MODEL = "small.en"

pytestmark = [pytest.mark.slow, needs_ffmpeg,
              pytest.mark.skipif(not is_downloaded(MODEL), reason=f"{MODEL} not downloaded")]


@pytest.fixture(scope="module")
def transcript(tmp_path_factory, sample_meta_module):
    import os

    from cutroom.ingest.ingest import ingest
    from cutroom.job import Job
    from conftest import SAMPLE_MP4

    base = tmp_path_factory.mktemp("jobs")
    os.environ["CUTROOM_JOBS_DIR"] = str(base)
    try:
        job = Job.create("asr-test", base=base)
        ingest(job, [SAMPLE_MP4])
        src = job.manifest.sources[0]
        extract_audio(job, src)
        asr = ASRSettings(model=MODEL, cpu_threads=4)
        t = transcribe_audio(job.abspath(src.audio["asr"]), asr, source_id=src.id)
        return t, job
    finally:
        os.environ.pop("CUTROOM_JOBS_DIR", None)


@pytest.fixture(scope="module")
def sample_meta_module():
    from conftest import SAMPLE_META

    return SAMPLE_META


def test_words_match_script(transcript, sample_meta_module):
    t, _ = transcript
    assert word_error_rate(sample_meta_module["reference_text"], t.text) <= 0.15


def test_word_timestamps_are_sane(transcript, sample_meta_module):
    t, _ = transcript
    words = t.words
    assert len(words) > 50
    assert all(w.start <= w.end for w in words)
    assert all(a.start <= b.start + 1e-6 for a, b in zip(words, words[1:]))
    assert words[-1].end <= t.duration + 0.05


def test_sentence_boundaries_close_to_truth(transcript, sample_meta_module):
    t, _ = transcript
    errs = boundary_errors(t, sample_meta_module["sentences"])
    assert max(errs) <= 0.5


def test_retake_is_kept_verbatim(transcript):
    t, _ = transcript
    assert t.text.lower().count("let the dough rest") == 2


def test_outputs_round_trip(transcript, tmp_path):
    t, _ = transcript
    paths = save_transcript(t, tmp_path)
    assert load_transcript(paths["json"]).words == t.words
    txt = readable_text(t)
    assert txt.startswith("# Transcript") and "[00:00:0" in txt
    srt = to_srt(t)
    assert srt.startswith("1\n00:00:0") and "-->" in srt
