"""Shared CLI plumbing: output conventions, progress bars, job command logging."""

from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from typing import Annotated, Iterator, Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn

out = Console()
err = Console(stderr=True)

JsonOpt = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON on stdout.")]
JobArg = Annotated[str, typer.Argument(help="Job id (folder name under jobs/) or path to a job folder.")]
SourcesOpt = Annotated[Optional[list[str]], typer.Option("--source", "-s", help="Source id(s), e.g. S01. Default: all.")]
VersionOpt = Annotated[Optional[str], typer.Option("--version", "-v", help="Timeline version (v003, 3, HEAD, HEAD~1). Default: HEAD.")]
DryRunOpt = Annotated[bool, typer.Option("--dry-run", help="Show what would change without saving a new version.")]


def emit(as_json: bool, data: dict) -> None:
    if as_json:
        out.print_json(json.dumps(data, default=str))


@contextmanager
def progress_bar(label: str, enabled: bool = True) -> Iterator:
    """Yields a callback taking a 0..1 fraction. Draws on stderr; silent when disabled."""
    if not enabled or not err.is_terminal:
        yield lambda f: None
        return
    with Progress(TextColumn("[bold]{task.description}"), BarColumn(), TaskProgressColumn(),
                  TimeElapsedColumn(), console=err, transient=True) as p:
        task = p.add_task(label, total=1.0)
        yield lambda f: p.update(task, completed=f)


@contextmanager
def job_command(job, name: str) -> Iterator[dict]:
    """Time a command, and record it in the job's command log whatever happens."""
    summary: dict = {}
    t0 = time.perf_counter()
    status = "failed"
    try:
        yield summary
        status = "ok"
    finally:
        job.log.command(name, sys.argv[1:], status, time.perf_counter() - t0, summary)
