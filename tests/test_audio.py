"""Voice chain decisions and measured loudness on target."""

import json
import subprocess

import numpy as np
import pytest
import soundfile as sf
from typer.testing import CliRunner

from cutroom.audio.chain import apply_chain, chain_filters, plan_chain
from cutroom.cli import app
from cutroom.perception.audio import measure_loudness
from cutroom.style import load_style

from conftest import SAMPLE_MP4, needs_ffmpeg

STYLE = load_style()


def test_clean_recording_gets_no_denoise():
    c = plan_chain({"snr_db": 45, "noise_floor_dbfs": -65, "speech_level_dbfs": -20}, STYLE)
    assert "denoise" not in c and any("no denoise" in r for r in c["reasons"])
    assert c["compressor"]["threshold_db"] == -28.0 and c["target_lufs"] == -14.0


def test_noisy_recording_gets_proportionate_denoise():
    mild = plan_chain({"snr_db": 30, "noise_floor_dbfs": -50, "speech_level_dbfs": -20}, STYLE)
    bad = plan_chain({"snr_db": 15, "noise_floor_dbfs": -35, "speech_level_dbfs": -20}, STYLE)
    assert mild["denoise"]["nr"] < bad["denoise"]["nr"] <= 12


def test_filters_are_valid_ffmpeg_syntax():
    f = chain_filters(plan_chain({"snr_db": 20, "noise_floor_dbfs": -45, "speech_level_dbfs": -22}, STYLE))
    assert f[0].startswith("highpass") and any(x.startswith("afftdn") for x in f) and f[-1].startswith("deesser")


@needs_ffmpeg
@pytest.mark.parametrize("gain_db", [-18.0, 0.0])
def test_chain_hits_loudness_target_from_quiet_and_loud_input(tmp_path, gain_db):
    wav = tmp_path / "in.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(SAMPLE_MP4), "-ac", "2", "-ar", "48000",
                    "-af", f"volume={gain_db}dB", str(wav)], check=True)
    chain = plan_chain({"snr_db": 40, "noise_floor_dbfs": -60, "speech_level_dbfs": -25}, STYLE)
    res = apply_chain(wav, tmp_path / "out.wav", chain)
    assert res["after_lufs"] == pytest.approx(-14.0, abs=0.7)
    assert res["after_true_peak_dbtp"] <= -1.0


@needs_ffmpeg
def test_render_with_voice_chain_passes_loudness_checks(ingested_job):
    from cutroom.ingest.proxy import extract_audio, make_proxy
    from cutroom.perception.pipeline import audio_report
    from cutroom.render.engine import render
    from cutroom.timeline import store
    from cutroom.timeline.ops import assemble

    job = ingested_job
    s = job.manifest.sources[0]
    make_proxy(job, s)
    extract_audio(job, s)
    tl = assemble(job)
    tl.audio.voice_chain = plan_chain(audio_report(job, s), STYLE)
    tl = store.load(job, store.save_new(job, tl, command="t", summary="t"))
    rep = render(job, tl, mode="preview", timecode=False)
    assert rep["checks"]["loudness_on_target"] and rep["checks"]["true_peak_ok"], rep["loudness"]
    assert rep["audio_chain"]["before_lufs"] < -16          # the sample starts quieter than the target


@needs_ffmpeg
def test_loudness_command(tmp_path):
    r = CliRunner().invoke(app, ["loudness", str(SAMPLE_MP4), "--json"])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)
    assert -40 < d["integrated_lufs"] < -5 and d["true_peak_dbtp"] < 0
