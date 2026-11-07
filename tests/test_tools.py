"""Plugin registry rules and the ffmpeg passthrough guard."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cutroom.cli import app
from cutroom.tools.registry import manifests
from cutroom.tools.ffmpeg_tool import _output_candidates

from conftest import needs_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_every_tool_has_tests_and_help():
    ms = manifests()
    assert len(ms) >= 15
    for m in ms:
        assert m.tests, f"{m.name} lists no tests"
        for t in m.tests:
            assert (ROOT / t).is_file(), f"{m.name}: test file {t} is missing"
        r = runner.invoke(app, [m.name, "--help"])
        assert r.exit_code == 0 and "Usage" in r.output, m.name


def test_timeline_changing_tools_offer_dry_run_or_are_versioned():
    names = {m.name for m in manifests() if m.changes_timeline}
    assert {"remove-silence", "remove-fillers", "detect-retakes", "cut", "rough-cut"} <= names
    for n in ("remove-silence", "remove-fillers", "detect-retakes", "cut"):
        assert "--dry-run" in runner.invoke(app, [n, "--help"]).output


def test_output_candidates_include_outputs_after_flags():
    args = ["-i", "in.mp4", "-t", "1", "-y", "out.mp4"]
    assert "out.mp4" in _output_candidates(args) and "in.mp4" not in _output_candidates(args)


@needs_ffmpeg
def test_ffmpeg_passthrough_protects_sources_and_outside(ingested_job, tmp_path):
    job = ingested_job
    src = job.manifest.sources[0].path
    r = runner.invoke(app, ["ffmpeg", job.id, "--", "-i", src, "-t", "1", "-y", src])
    assert r.exit_code != 0 and "source footage" in str(r.exception or r.output)
    r = runner.invoke(app, ["ffmpeg", job.id, "--", "-i", src, "-t", "1", "-y", str(tmp_path / "x.mp4")])
    assert r.exit_code != 0
    r = runner.invoke(app, ["ffmpeg", job.id, "--", "-y", "-i", src, "-t", "0.5", "renders/ok.mp4"])
    assert r.exit_code == 0, r.output
    assert (job.root / "renders" / "ok.mp4").is_file()
    assert "ffmpeg passthrough" in (job.root / "log.md").read_text()
