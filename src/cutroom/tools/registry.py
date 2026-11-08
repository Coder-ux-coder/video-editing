"""Tool plugins: drop a module into ``cutroom/tools/`` and its commands appear in the CLI.

A tool module defines one or more functions decorated with :func:`tool`, each carrying a
:class:`ToolManifest`. ``register_all`` imports every module in the package and adds the
commands to the Typer app; ``docs/TOOLS.md`` lists the manifests. A test checks that every
manifest names test files that exist, so no tool ships untested.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import asdict, dataclass, field
from typing import Callable

import typer


@dataclass(frozen=True)
class ToolManifest:
    name: str                                  # CLI command, e.g. "remove-silence"
    summary: str
    group: str                                 # ingest, analysis, timeline, cutting, render, utility
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)   # repository-relative test files
    changes_timeline: bool = False             # writes a new timeline version

    def to_dict(self) -> dict:
        return asdict(self)


REGISTRY: dict[str, tuple[ToolManifest, Callable]] = {}


def tool(manifest: ToolManifest) -> Callable[[Callable], Callable]:
    def deco(fn: Callable) -> Callable:
        if manifest.name in REGISTRY and REGISTRY[manifest.name][1] is not fn:
            raise RuntimeError(f"Two tools named '{manifest.name}'")
        REGISTRY[manifest.name] = (manifest, fn)
        return fn
    return deco


def load_plugins() -> None:
    import cutroom.tools as pkg

    for m in pkgutil.iter_modules(pkg.__path__):
        if not m.name.startswith("_") and m.name != "registry":
            importlib.import_module(f"{pkg.__name__}.{m.name}")


SELF_LOGGED = {"analyze", "ffmpeg", "render"}         # these record richer summaries themselves


def _logged(name: str, fn: Callable) -> Callable:
    """Record every run of a job command (status, seconds) in the job's command log, so
    `cutroom report` can say where the time went. Logging never changes the command's outcome."""
    import functools
    import inspect
    import sys
    import time

    params = list(inspect.signature(fn).parameters)
    if name in SELF_LOGGED or not params or params[0] != "job_id":
        return fn

    @functools.wraps(fn)
    def run(*args, **kwargs):
        job_id = kwargs.get("job_id", args[0] if args else None)
        t0 = time.perf_counter()
        status = "failed"
        try:
            out = fn(*args, **kwargs)
            status = "ok"
            return out
        except typer.Exit as e:
            status = "ok" if not e.exit_code else "failed"
            raise
        finally:
            try:
                from cutroom.job import Job

                Job.load(job_id).log.command(name, sys.argv[1:], status, time.perf_counter() - t0)
            except Exception:  # noqa: BLE001 (no job, read-only folder: the command still stands)
                pass
    return run


def register_all(app: typer.Typer) -> None:
    load_plugins()
    for name, (manifest, fn) in sorted(REGISTRY.items()):
        app.command(name=name, help=fn.__doc__ or manifest.summary)(_logged(name, fn))


def manifests() -> list[ToolManifest]:
    load_plugins()
    return [m for m, _ in sorted(REGISTRY.values(), key=lambda x: (x[0].group, x[0].name))]
