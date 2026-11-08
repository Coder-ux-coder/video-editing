"""Operations: the job report (time, cost, network) and the batch queue."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from cutroom.cli_common import JobArg, JsonOpt, emit, err, out
from cutroom.job import Job
from cutroom.tools.registry import ToolManifest, tool

TESTS = ["tests/test_hardening.py"]


@tool(ToolManifest("report", "Where a job's time went (commands, render stages), its cost ($0) and network use.",
                   "utility", inputs=["logs/commands.jsonl", "renders/*.json", "timeline HEAD"],
                   outputs=["logs/report.md", "logs/report.json"], tests=TESTS))
def report(job_id: JobArg, as_json: JsonOpt = False) -> None:
    """Every logged command with its time and failures, each full render's stage timings and
    self-checks, the HEAD review scorecard, the playbook stages, the cost (always $0.00: nothing
    is paid) and what reached the network (free models, licensed assets; never client media)."""
    from cutroom.report import write_report

    job = Job.load(job_id)
    r, md = write_report(job)
    emit(as_json, r)
    if not as_json:
        out.print(md)


@tool(ToolManifest("batch", "Run a file of cutroom commands one after another; re-running resumes.",
                   "utility", inputs=["a text file, one command per line"], outputs=["<file>.state.json"], tests=TESTS))
def batch(file: Annotated[Path, typer.Argument(help="Text file: one cutroom command per line, # comments.")],
          keep_going: Annotated[bool, typer.Option("--keep-going", help="Continue after a failed line.")] = False,
          again: Annotated[bool, typer.Option("--again", help="Forget earlier successes and run every line.")] = False,
          dry_run: Annotated[bool, typer.Option("--dry-run", help="List what would run.")] = False,
          as_json: JsonOpt = False) -> None:
    """Each line runs as its own process; the outcome of every line is kept in <file>.state.json.
    Run the same batch again and finished lines are skipped (an interrupted render resumes its
    own finished parts). Stops at the first failure unless --keep-going. `approve` is refused:
    a person approves deliveries."""
    from cutroom.batch import run_batch

    res = run_batch(file, keep_going=keep_going, again=again, dry_run=dry_run,
                    log=(lambda s: None) if as_json else (lambda s: err.print(f"[bold]{s}[/]")))
    emit(as_json, res)
    if not as_json:
        for ln in res["lines"]:
            colour = {"ok": "green", "FAILED": "red"}.get(ln["status"], "dim")
            t = f" ({ln['seconds']}s)" if ln.get("seconds") is not None else ""
            out.print(f"[{colour}]{ln['status']:>22}[/] line {ln['line']}: {ln['command']}{t}")
            if ln["status"] == "FAILED":
                out.print(f"    {ln['tail'].strip().splitlines()[-1] if ln['tail'].strip() else ''}")
        out.print(f"{res['ok']} ok, {res['failed']} failed, {res['skipped']} skipped, {res['not_run']} not run.")
    if res["failed"]:
        raise typer.Exit(1)
