"""Escape hatch: run any FFmpeg command inside a job, logged, without risking the sources."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Annotated

import typer

from cutroom.cli_common import JobArg, JsonOpt, emit, job_command, out
from cutroom.errors import RefusedError
from cutroom.job import Job
from cutroom.system.ffmpeg import run_ffmpeg
from cutroom.tools.registry import ToolManifest, tool


def _output_candidates(args: list[str]) -> list[str]:
    """Every argument that could be an output file: anything not an option and not an input.

    Deliberately over-inclusive (option values like "1" or "libx264" are included too): a
    false candidate only gets a harmless path check, while a missed output could overwrite
    footage. Knowing which FFmpeg options take values is not needed for this to be safe.
    """
    outs = []
    for k, a in enumerate(args):
        if a.startswith("-") and a not in ("-",):
            continue
        if k > 0 and args[k - 1] == "-i":
            continue
        outs.append(a)
    return outs


def _looks_like_path(a: str) -> bool:
    """'renders/x.mp4', 'out.wav', 'C:\\x.mp4' yes; '0.5', 'scale=320:-2', 'libx264' no."""
    if "=" in a or re.fullmatch(r"[\d.:+-]+", a):
        return False
    return "/" in a or "\\" in a or bool(re.fullmatch(r"\.[A-Za-z][A-Za-z0-9]{0,4}", Path(a).suffix))


@tool(ToolManifest("ffmpeg", "Run any FFmpeg command in the job folder (logged; sources protected).", "utility",
                   inputs=["any"], outputs=["files under the job folder"], tests=["tests/test_tools.py"]))
def ffmpeg(job_id: JobArg,
           args: Annotated[list[str], typer.Argument(help="FFmpeg arguments after `--`, e.g. -- -i source/S01.mp4 -t 5 renders/x.mp4")],
           as_json: JsonOpt = False) -> None:
    """Passthrough for anything no tool covers yet. Runs in the job folder; relative paths are
    job-relative. Refuses to write outside the job or over any source file. The full command is
    logged so it can be turned into a proper tool later (the self-extension rule)."""
    job = Job.load(job_id)
    root = job.root.resolve()
    sources = {job.abspath(s.path).resolve() for s in job.manifest.sources}
    for o in _output_candidates(list(args)):
        if o in ("-", "pipe:1") or not _looks_like_path(o):
            continue
        op = (root / o).resolve() if not Path(o).is_absolute() else Path(o).resolve()
        if op in sources:
            raise RefusedError(f"Refusing to write to source footage {op}.", fix="Write to renders/ or analysis/ instead.")
        if root not in op.parents and not op.is_file():
            raise RefusedError(f"Output {op} is outside the job folder.", fix=f"Write under {root}.")
    with job_command(job, "ffmpeg") as summary:
        log = run_ffmpeg(list(args), desc="ffmpeg passthrough", cwd=root)
        summary["args"] = args
    job.log.section("ffmpeg passthrough")
    job.log.note(f"`ffmpeg {shlex.join(args)}`")
    emit(as_json, {"ok": True, "args": args, "log_tail": log.strip().splitlines()[-5:]})
    if not as_json:
        out.print("[green]done[/]")
