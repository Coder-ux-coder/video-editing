import json

from typer.testing import CliRunner

from cutroom.cli import app

from conftest import needs_ffmpeg

runner = CliRunner()


def test_help_lists_commands():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for cmd in ("doctor", "ingest", "proxy", "transcribe", "new", "models"):
        assert cmd in r.output


def test_tools_md_is_up_to_date():
    r = runner.invoke(app, ["docs", "--check"])
    assert r.exit_code == 0, "docs/TOOLS.md is stale: run `cutroom docs`"


def test_new_and_status_json(jobs_base):
    r = runner.invoke(app, ["new", "cli-job", "--client", "acme", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["job"] == "cli-job"
    r = runner.invoke(app, ["status", "cli-job", "--json"])
    assert json.loads(r.output)["client"] == "acme"
    r = runner.invoke(app, ["jobs", "--json"])
    assert [j["id"] for j in json.loads(r.output)["jobs"]] == ["cli-job"]


@needs_ffmpeg
def test_ingest_and_proxy_via_cli(jobs_base, sample_mp4):
    assert runner.invoke(app, ["new", "cli-media"]).exit_code == 0
    r = runner.invoke(app, ["ingest", "cli-media", str(sample_mp4), "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["results"][0]["status"] == "added"
    r = runner.invoke(app, ["proxy", "cli-media", "--json"])
    assert r.exit_code == 0, r.output
    statuses = [x["status"] for x in json.loads(r.output)["results"]]
    assert statuses == ["created", "created"]
    log = (jobs_base / "cli-media" / "logs" / "commands.jsonl").read_text().splitlines()
    assert [json.loads(line)["command"] for line in log] == ["ingest", "proxy"]


def test_models_list_json():
    r = runner.invoke(app, ["models", "list", "--json"])
    names = [m["name"] for m in json.loads(r.output)["models"]]
    assert "small.en" in names and all(m["licence"] == "MIT" for m in json.loads(r.output)["models"])


def test_tools_md_documents_every_command():
    import typer.main

    from cutroom.docsgen import render_tools_md, walk_commands

    names = [n for n, _ in walk_commands(typer.main.get_command(app))]
    assert len(names) >= 12 and "cutroom models pull" in names
    text = render_tools_md()
    for n in names:
        assert f"## `{n}`" in text
    assert "`--json`" in text and "`JOB_ID`" in text
