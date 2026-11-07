"""The `cutroom` command line. Small, documented commands; JSON output on request.

Conventions every command follows:
- ``--json`` prints one JSON object on stdout (progress and logs go to stderr).
- Errors print what went wrong and a suggested fix, and exit non-zero.
- Commands that touch a job append to its ``log.md`` and ``logs/commands.jsonl``.
"""

from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Iterator, Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from cutroom import __version__
from cutroom.errors import CutroomError

app = typer.Typer(
    name="cutroom",
    help="Cutroom: agent-native, non-destructive video editing. Run `cutroom doctor` first.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,
    add_completion=False,
)
models_app = typer.Typer(help="Manage local speech-recognition models.", no_args_is_help=True)
app.add_typer(models_app, name="models")

out = Console()
err = Console(stderr=True)

JsonOpt = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON on stdout.")]
JobArg = Annotated[str, typer.Argument(help="Job id (folder name under jobs/) or path to a job folder.")]
SourcesOpt = Annotated[Optional[list[str]], typer.Option("--source", "-s", help="Source id(s), e.g. S01. Default: all.")]


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


# --------------------------------------------------------------------------- doctor / machine


@app.command()
def doctor(
    quick: Annotated[bool, typer.Option(help="Skip the end-to-end ingest/proxy/transcribe smoke test.")] = False,
    select_asr: Annotated[bool, typer.Option("--select-asr", help="Benchmark speech models on this machine and pick the best that fits.")] = False,
    asr_model: Annotated[Optional[str], typer.Option(help="Force a speech model (see `cutroom models list`).")] = None,
    download: Annotated[bool, typer.Option(help="Download the chosen speech model if missing.")] = True,
    as_json: JsonOpt = False,
) -> None:
    """Check the whole toolchain on this machine and write config/machine.json.

    Detects OS, CPU, RAM, GPU and FFmpeg capabilities, tests hardware encoders,
    then ingests, proxies and transcribes a bundled clip and checks the words and
    timings against its known script. Exit code 0 means everything works.
    """
    from cutroom.doctor import run_doctor

    log = (lambda s: None) if as_json else (lambda s: err.print(f"[dim]· {s}[/dim]"))
    report = run_doctor(quick=quick, select_asr=select_asr, asr_model=asr_model, download=download, log=log)
    if as_json:
        emit(True, report.to_dict())
    else:
        colors = {"ok": "green", "warn": "yellow", "fail": "red", "skip": "dim"}
        t = Table(title=f"cutroom doctor (v{__version__})", show_lines=False)
        t.add_column("check")
        t.add_column("status")
        t.add_column("detail", overflow="fold")
        for c in report.checks:
            detail = c.detail + (f"\n[italic]fix:[/italic] {c.fix}" if c.fix and c.status in ("warn", "fail") else "")
            t.add_row(c.name, f"[{colors[c.status]}]{c.status.upper()}[/]", detail)
        out.print(t)
        if report.asr_benchmark:
            b = Table(title="Speech model benchmark (this machine)")
            cols = ("rtf", "wer_synthetic", "wer_real", "timing_err_max_s", "stumble_synthetic", "stumble_real",
                    "peak_rss_gb")
            for col in ("model", *cols):
                b.add_column(col)
            for row in report.asr_benchmark:
                b.add_row(row["model"], *(str(row.get(k, row.get("error", ""))) for k in cols))
            out.print(b)
        if report.profile:
            a = report.profile["asr"]
            out.print(f"Speech model: [bold]{a['model']}[/] on {a['device']} ({a['compute_type']}); "
                      f"profile {'saved to config/machine.json' if report.ok else 'NOT saved (fix failures first)'}")
        out.print("[bold green]All checks passed.[/]" if report.ok else "[bold red]Some checks failed; see fixes above.[/]")
    raise typer.Exit(0 if report.ok else 1)


@app.command()
def machine(as_json: JsonOpt = False) -> None:
    """Show the machine profile (hardware, chosen models, encoders, concurrency)."""
    from cutroom.paths import machine_profile_path
    from cutroom.system.profile import load_profile

    p = load_profile()
    data = json.loads(p.model_dump_json())
    data["_saved"] = machine_profile_path().is_file()
    if as_json:
        emit(True, data)
        return
    m = p.machine
    out.print(f"[bold]{m['os']['pretty']}[/] {m['os']['arch']} · {m['cpu']['model']} "
              f"({m['cpu']['physical_cores']} cores) · {m['ram_total_gb']} GB RAM")
    out.print(f"GPUs: {', '.join(g['name'] for g in m['gpus']) or 'none'} · CUDA devices: {p.cuda_devices}")
    out.print(f"ASR: {p.asr.model} on {p.asr.device}/{p.asr.compute_type}, {p.asr.cpu_threads} threads "
              f"({p.asr.selection.get('method')}: {p.asr.selection.get('reason')})")
    out.print(f"Render: proxy {p.render.proxy_short_side}p {p.render.proxy_encoder}, preview {p.render.preview_encoder}, "
              f"final {p.render.final_encoder}, concurrency {p.render.concurrency}, memory budget {p.memory_budget_gb} GB")
    if not data["_saved"]:
        out.print("[yellow]Not saved yet: run `cutroom doctor`.[/]")


# --------------------------------------------------------------------------- models


@models_app.command("list")
def models_list(as_json: JsonOpt = False) -> None:
    """List available speech models, their size, licence and download state."""
    from cutroom.perception.models import ASR_MODELS, is_downloaded

    rows = [{**m.to_dict(), "downloaded": is_downloaded(m.name)} for m in ASR_MODELS.values()]
    if as_json:
        emit(True, {"models": rows})
        return
    t = Table(title="Speech models (all local, free)")
    for col in ("name", "MB", "english-only", "licence", "downloaded", "notes"):
        t.add_column(col)
    for r in rows:
        t.add_row(r["name"], str(r["download_mb"]), "yes" if r["english_only"] else "no", r["licence"],
                  "[green]yes[/]" if r["downloaded"] else "no", r["notes"])
    out.print(t)


@models_app.command("pull")
def models_pull(name: Annotated[str, typer.Argument(help="Model name, e.g. small.en")], as_json: JsonOpt = False) -> None:
    """Download a speech model (pinned revision) into models/asr/."""
    from cutroom.perception.models import ensure_model, get_spec

    spec = get_spec(name)
    if not as_json:
        err.print(f"Downloading {spec.name} from {spec.repo}@{spec.revision[:8]} (~{spec.download_mb} MB) ...")
    path = ensure_model(name)
    emit(as_json, {"model": name, "path": str(path)})
    if not as_json:
        out.print(f"[green]Ready:[/] {path}")


# --------------------------------------------------------------------------- jobs


@app.command()
def new(
    job_id: Annotated[str, typer.Argument(help="New job id, e.g. acme-launch-video")],
    client: Annotated[Optional[str], typer.Option(help="Client name (selects styles/clients/<client>.style.yaml later).")] = None,
    title: Annotated[Optional[str], typer.Option(help="Working title.")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Create a job workspace: jobs/<job-id>/ with brief.md, log.md and stage folders."""
    from cutroom.job import Job

    job = Job.create(job_id, client=client, title=title)
    emit(as_json, {"job": job_id, "path": str(job.root)})
    if not as_json:
        out.print(f"[green]Created[/] {job.root}\nNext: fill in {job.root / 'brief.md'}, then "
                  f"`cutroom ingest {job_id} <footage>`")


@app.command()
def jobs(as_json: JsonOpt = False) -> None:
    """List jobs."""
    from cutroom.job import list_jobs

    rows = [{"id": m.id, "client": m.client, "created": m.created_at, "sources": len(m.sources),
             "stages": {k: v.status for k, v in m.stages.items()}} for m in list_jobs()]
    if as_json:
        emit(True, {"jobs": rows})
        return
    if not rows:
        out.print("No jobs yet. Create one with `cutroom new <job-id>`.")
        return
    t = Table(title="Jobs")
    for col in ("id", "client", "sources", "stages", "created"):
        t.add_column(col)
    for r in rows:
        t.add_row(r["id"], r["client"] or "", str(r["sources"]),
                  ", ".join(f"{k}:{v}" for k, v in r["stages"].items()), r["created"])
    out.print(t)


@app.command()
def status(job_id: JobArg, as_json: JsonOpt = False) -> None:
    """Show a job's sources, derived files and stage status."""
    from cutroom.job import Job

    job = Job.load(job_id)
    m = job.manifest
    data = json.loads(m.model_dump_json())
    if as_json:
        emit(True, data)
        return
    out.print(f"[bold]{m.id}[/] (client: {m.client or '-'}) at {job.root}")
    t = Table()
    for col in ("source", "file", "media", "role", "proxy", "audio", "transcript"):
        t.add_column(col, overflow="fold")
    for s in m.sources:
        t.add_row(s.id, s.name, s.media.summary(), s.role, "yes" if s.proxy else "-",
                  "yes" if s.audio.get("asr") else "-", "yes" if s.transcript else "-")
    out.print(t)
    for k, v in m.stages.items():
        out.print(f"  stage {k}: {v.status} ({v.updated_at})")


@app.command()
def probe(path: Annotated[Path, typer.Argument(help="Media file to inspect.")], as_json: JsonOpt = False) -> None:
    """Inspect a media file (streams, fps, VFR, HDR, rotation, duration) without ingesting it."""
    from cutroom.ingest.probe import probe as do_probe

    info = do_probe(path)
    if as_json:
        emit(True, json.loads(info.model_dump_json()))
    else:
        out.print(f"{path}: {info.summary()}")


@app.command()
def ingest(
    job_id: JobArg,
    paths: Annotated[Optional[list[Path]], typer.Argument(help="Files or folders. Default: files already in jobs/<id>/source/.")] = None,
    reference: Annotated[bool, typer.Option("--reference", help="Read files in place instead of copying them into the job (saves disk; breaks if files move).")] = False,
    role: Annotated[Optional[str], typer.Option(help="Role for these files: main, broll, music, sfx, voiceover, image.")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Probe, hash and register footage in a job. Originals are never modified.

    By default files are copied into jobs/<id>/source/ (read-only) so the job is
    self-contained. Identical files (same hash) are detected and skipped.
    """
    from cutroom.ingest.ingest import ingest as do_ingest
    from cutroom.job import Job

    job = Job.load(job_id)
    with job_command(job, "ingest") as summary, progress_bar("ingest", not as_json) as cb:
        total = sum(p.stat().st_size for p in (paths or []) if p.is_file()) or None
        done = [0]

        def on_bytes(n: int) -> None:
            done[0] += n
            if total:
                cb(done[0] / total)

        results = do_ingest(job, list(paths or []), copy=not reference, role=role, on_bytes=on_bytes)
        summary["results"] = [r.to_dict() for r in results]
    job.mark_stage("ingest", "done", sources=len(job.manifest.sources))
    if as_json:
        emit(True, {"job": job.id, "results": [r.to_dict() for r in results]})
    else:
        for r in results:
            color = {"added": "green", "duplicate": "yellow", "failed": "red"}[r.status]
            out.print(f"[{color}]{r.status:9}[/] {r.source_id or '-':4} {r.path}\n           {r.message}")
    if any(r.status == "failed" for r in results):
        raise typer.Exit(4)


@app.command()
def proxy(
    job_id: JobArg,
    sources: SourcesOpt = None,
    short_side: Annotated[Optional[int], typer.Option(help="Proxy short side in pixels (default from machine profile, 540).")] = None,
    force: Annotated[bool, typer.Option(help="Rebuild even if up to date.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Make constant-frame-rate proxies and extract audio (16 kHz WAV for ASR, 48 kHz FLAC for analysis)."""
    from cutroom.ingest.proxy import extract_audio, make_proxy
    from cutroom.job import Job
    from cutroom.system.profile import load_profile

    job = Job.load(job_id)
    side = short_side or load_profile().render.proxy_short_side
    results = []
    with job_command(job, "proxy") as summary:
        job.log.section(f"proxy ({side}p)")
        for s in job.select_sources(sources):
            with progress_bar(f"proxy {s.id}", not as_json) as cb:
                results.append(make_proxy(job, s, short_side=side, force=force, on_progress=cb))
            with progress_bar(f"audio {s.id}", not as_json) as cb:
                results.append(extract_audio(job, s, force=force, on_progress=cb))
        summary["results"] = results
    job.mark_stage("proxy", "done")
    if as_json:
        emit(True, {"job": job.id, "results": results})
    else:
        for r in results:
            out.print(f"{r['source']}: {r['status']} " + ", ".join(f"{k}={v}" for k, v in r.items() if k not in ("source", "status")))


@app.command()
def transcribe(
    job_id: JobArg,
    sources: SourcesOpt = None,
    model: Annotated[Optional[str], typer.Option(help="Override the speech model from the machine profile.")] = None,
    hotword: Annotated[Optional[list[str]], typer.Option("--hotword", help="Names/terms to bias recognition toward (repeatable).")] = None,
    verbatim: Annotated[bool, typer.Option(help="Keep fillers, stumbles and repetitions (needed for cutting them).")] = True,
    force: Annotated[bool, typer.Option(help="Re-transcribe even if a transcript exists.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Transcribe sources locally with word-level timestamps.

    Writes analysis/<source>/transcript.json (words, times, confidence),
    transcript.txt (timestamped reading view; doubtful words marked) and
    transcript.srt. Runs `proxy` audio extraction first if needed.
    """
    from cutroom.ingest.proxy import extract_audio
    from cutroom.job import Job
    from cutroom.paths import rel_or_abs
    from cutroom.perception.transcribe import save_transcript, transcribe_audio
    from cutroom.system.profile import load_profile

    job = Job.load(job_id)
    asr = load_profile().asr
    if model:
        asr = asr.model_copy(update={"model": model})
    asr = asr.model_copy(update={"verbatim_prompt": verbatim})
    results = []
    with job_command(job, "transcribe") as summary:
        job.log.section(f"transcribe ({asr.model}, {asr.device}/{asr.compute_type})")
        for s in job.select_sources(sources):
            if s.media.primary_audio is None:
                results.append({"source": s.id, "status": "skipped", "reason": "no audio"})
                continue
            out_dir = job.analysis_dir(s.id)
            if s.transcript and not force and (job.root / s.transcript).is_file():
                results.append({"source": s.id, "status": "up-to-date", "path": s.transcript})
                continue
            if not s.audio.get("asr") or not job.abspath(s.audio["asr"]).is_file():
                extract_audio(job, s)
            with progress_bar(f"transcribe {s.id}", not as_json) as cb:
                t = transcribe_audio(job.abspath(s.audio["asr"]), asr, hotwords=hotword,
                                     time_offset=s.media.primary_audio.start_time, source_id=s.id, on_progress=cb)
            paths = save_transcript(t, out_dir)
            s.transcript = rel_or_abs(paths["json"], job.root)
            job.save()
            st = t.stats()
            job.log.note(f"{s.id}: {st['words']} words in {st['segments']} segments, "
                         f"{st['low_confidence_words']} low-confidence; {t.elapsed_s}s (RTF {t.rtf}). "
                         f"Read `{rel_or_abs(paths['txt'], job.root)}`.")
            results.append({"source": s.id, "status": "created", **st,
                            "txt": rel_or_abs(paths["txt"], job.root), "json": s.transcript})
        summary["results"] = results
    job.mark_stage("transcribe", "done", model=asr.model)
    if as_json:
        emit(True, {"job": job.id, "model": asr.model, "results": results})
    else:
        for r in results:
            out.print(f"{r['source']}: {r['status']} " + ", ".join(f"{k}={v}" for k, v in r.items() if k not in ("source", "status")))


# --------------------------------------------------------------------------- docs / version


@app.command()
def docs(check: Annotated[bool, typer.Option(help="Exit 1 if docs/TOOLS.md is out of date instead of writing it.")] = False) -> None:
    """Regenerate docs/TOOLS.md from the commands' own help text."""
    from cutroom.docsgen import render_tools_md
    from cutroom.paths import docs_dir

    path = docs_dir() / "TOOLS.md"
    text = render_tools_md()
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if check:
        if current != text:
            err.print("[red]docs/TOOLS.md is out of date; run `cutroom docs`.[/]")
            raise typer.Exit(1)
        out.print("docs/TOOLS.md is up to date.")
        return
    path.write_text(text, encoding="utf-8", newline="\n")
    out.print(f"Wrote {path}")


@app.command()
def version() -> None:
    """Print the Cutroom version."""
    out.print(__version__)


def main() -> None:
    try:
        app()
    except CutroomError as e:
        if "--json" in sys.argv:
            print(json.dumps(e.to_dict()))
        else:
            err.print(f"[bold red]Error:[/] {e.message}")
            if e.details:
                err.print(f"[dim]{e.details}[/dim]")
            if e.fix:
                err.print(f"[bold]Fix:[/] {e.fix}")
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        err.print("Interrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
