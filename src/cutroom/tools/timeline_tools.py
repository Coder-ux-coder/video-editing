"""Timeline commands: assemble, show, validate, diff, versions, undo, checkout, snapshot, style."""

from __future__ import annotations

import json
from typing import Annotated, Literal, Optional

import typer

from cutroom.cli_common import JobArg, JsonOpt, SourcesOpt, VersionOpt, emit, out
from cutroom.errors import RefusedError
from cutroom.job import Job
from cutroom.tools.registry import ToolManifest, tool
from cutroom.timeline import store
from cutroom.timeline.diff import diff, diff_text
from cutroom.timeline.model import timeline_warnings, validate_timeline
from cutroom.timeline.ops import assemble as do_assemble
from cutroom.timeline.ops import check_cut_points, words_for
from cutroom.timeline.outline import cut_list, outline, summary, tc

TESTS = ["tests/test_timeline.py"]


@tool(ToolManifest("assemble", "Create the first timeline: every main source, whole, in order.", "timeline",
                   inputs=["job sources"], outputs=["timeline v001 (or next version)"], tests=TESTS,
                   changes_timeline=True))
def assemble(job_id: JobArg, sources: SourcesOpt = None,
             style: Annotated[str, typer.Option(help="Style name (styles/<name>.style.yaml).")] = "default",
             as_json: JsonOpt = False) -> None:
    """Create a timeline from the job's main sources (nothing removed yet). Saves a new version."""
    job = Job.load(job_id)
    tl = do_assemble(job, sources, style=style)
    n = store.save_new(job, tl, command="assemble",
                       summary=f"assembly of {', '.join(s.id for s in tl.sources)} ({tc(tl.duration)})",
                       parent=store.head(job))
    emit(as_json, {"version": n, **summary(store.load(job, n))})
    if not as_json:
        out.print(f"Assembled v{n:03d}: {len(tl.clips)} clip(s), {tc(tl.duration)}")


@tool(ToolManifest("show", "Print a timeline as an outline, cut list, summary or JSON.", "timeline",
                   inputs=["timeline version", "words.json"], outputs=["stdout"], tests=TESTS))
def show(job_id: JobArg, version: VersionOpt = None,
         format: Annotated[Literal["outline", "cuts", "summary", "json"], typer.Option(help="View to print.")] = "outline",
         as_json: JsonOpt = False) -> None:
    """Show a timeline version. The outline lists every clip with its timeline and source times and
    the words spoken, and between clips every cut with its reason: the main way to read an edit."""
    job = Job.load(job_id)
    tl = store.load(job, version)
    if as_json or format == "json":
        out.print_json(tl.model_dump_json(by_alias=True)) if format == "json" else emit(True, summary(tl))
        return
    if format == "summary":
        out.print(json.dumps(summary(tl), indent=1))
    elif format == "cuts":
        out.print(cut_list(tl), markup=False, highlight=False)
    else:
        words = {s.id: words_for(job, s.id) for s in tl.sources}
        out.print(outline(tl, words), markup=False, highlight=False)


@tool(ToolManifest("validate", "Validate a timeline (schema, structure, word-boundary cuts).", "timeline",
                   inputs=["timeline version"], outputs=["problems list"], tests=TESTS))
def validate(job_id: JobArg, version: VersionOpt = None, as_json: JsonOpt = False) -> None:
    """Check a timeline: schema, overlaps, source ranges, and that no cut lands inside a word.
    Warnings (graphics whose moment was cut, unsourced numbers) are listed but do not fail."""
    job = Job.load(job_id)
    tl = store.load(job, version)
    words = {s.id: words_for(job, s.id) for s in tl.sources}
    problems = validate_timeline(tl) + check_cut_points(tl, words)
    warnings = timeline_warnings(tl)
    emit(as_json, {"version": tl.meta.version, "valid": not problems, "problems": problems, "warnings": warnings})
    if not as_json:
        out.print(f"v{tl.meta.version:03d}: " + ("[green]valid[/]" if not problems else "[red]problems:[/]"))
        for p in problems:
            out.print(f"  - {p}")
        for w in warnings:
            out.print(f"  [yellow]! {w}[/]")
    if problems:
        raise typer.Exit(1)


@tool(ToolManifest("diff", "Human-readable change summary between two timeline versions.", "timeline",
                   inputs=["two timeline versions"], outputs=["stdout"], tests=TESTS))
def diff_cmd(job_id: JobArg, a: Annotated[str, typer.Argument(help="Older version, e.g. v003")],
             b: Annotated[Optional[str], typer.Argument(help="Newer version (default HEAD)")] = None,
             as_json: JsonOpt = False) -> None:
    """What changed between two versions: duration, clips, cuts added/restored, graphics, settings."""
    job = Job.load(job_id)
    ta, tb = store.load(job, a), store.load(job, b)
    if as_json:
        emit(True, diff(ta, tb))
    else:
        out.print(diff_text(ta, tb), markup=False, highlight=False)


@tool(ToolManifest("versions", "List timeline versions with their changelog lines.", "timeline",
                   inputs=["versions/"], outputs=["stdout"], tests=TESTS))
def versions(job_id: JobArg, as_json: JsonOpt = False) -> None:
    """List every timeline version, its parent, duration and what produced it. * marks HEAD."""
    job = Job.load(job_id)
    h = store.head(job)
    rows = []
    for n in store.list_versions(job):
        tl = store.load(job, n)
        last = tl.meta.changelog[-1] if tl.meta.changelog else None
        rows.append({"version": n, "parent": tl.meta.parent, "head": n == h, "duration_s": round(tl.duration, 3),
                     "command": last.command if last else "", "summary": last.summary if last else ""})
    if as_json:
        emit(True, {"versions": rows})
        return
    for r in rows:
        mark = "*" if r["head"] else " "
        parent = f"v{r['parent']:03d}" if r["parent"] else "-"
        out.print(f"{mark} v{r['version']:03d} (from {parent}) {tc(r['duration_s'])}  {r['summary']}",
                  markup=False, highlight=False)


@tool(ToolManifest("undo", "Move HEAD back to the parent version (nothing is deleted).", "timeline",
                   inputs=["versions/HEAD"], outputs=["versions/HEAD"], tests=TESTS))
def undo(job_id: JobArg, as_json: JsonOpt = False) -> None:
    """Undo the last edit: HEAD moves to the parent version. The undone version stays on disk."""
    job = Job.load(job_id)
    n = store.undo(job)
    emit(as_json, {"head": n})
    if not as_json:
        out.print(f"HEAD is now v{n:03d}")


@tool(ToolManifest("checkout", "Set HEAD to any version (later edits branch from it).", "timeline",
                   inputs=["versions/"], outputs=["versions/HEAD"], tests=TESTS))
def checkout(job_id: JobArg, version: Annotated[str, typer.Argument(help="Version to make current, e.g. v002")],
             as_json: JsonOpt = False) -> None:
    """Make a version current. The next edit creates a new version whose parent is this one."""
    job = Job.load(job_id)
    n = store.checkout(job, version)
    emit(as_json, {"head": n})
    if not as_json:
        out.print(f"HEAD is now v{n:03d}")


@tool(ToolManifest("snapshot", "Save the current version again as a named checkpoint.", "timeline",
                   inputs=["timeline HEAD"], outputs=["timeline vN+1"], tests=TESTS, changes_timeline=True))
def snapshot(job_id: JobArg, message: Annotated[str, typer.Option("--message", "-m", help="What this checkpoint is.")],
             as_json: JsonOpt = False) -> None:
    """Checkpoint HEAD as a new version with a message (e.g. 'sent to client for review')."""
    job = Job.load(job_id)
    tl = store.load(job)
    words = {s.id: words_for(job, s.id) for s in tl.sources}
    problems = validate_timeline(tl) + check_cut_points(tl, words)
    if problems:
        raise RefusedError("Refusing to snapshot an invalid timeline: " + "; ".join(problems[:3]),
                           fix="Run `cutroom validate` and fix the problems first.")
    n = store.save_new(job, tl, command="snapshot", summary=f"snapshot: {message}")
    emit(as_json, {"version": n})
    if not as_json:
        out.print(f"Snapshot saved as v{n:03d}")


@tool(ToolManifest("style", "Switch the style or client theme of an edit (graphics and captions restyle on render).",
                   "timeline", inputs=["timeline HEAD", "styles/*.style.yaml"], outputs=["timeline vN+1"],
                   tests=["tests/test_graphics.py"], changes_timeline=True))
def style(job_id: JobArg,
          name: Annotated[Optional[str], typer.Option("--name", help="Style (styles/<name>.style.yaml).")] = None,
          client_style: Annotated[Optional[str], typer.Option("--client-style", help="Client theme (styles/clients/<name>.style.yaml); 'none' to clear.")] = None,
          as_json: JsonOpt = False) -> None:
    """Every graphic and caption takes its colours, fonts, sizes and motion from the style's
    design tokens, so switching the theme restyles the whole edit without touching it."""
    from cutroom.style import list_styles, load_style, styles_dir

    job = Job.load(job_id)
    tl = store.load(job).model_copy(deep=True)
    if name:
        load_style(name)          # fails loudly with the list of styles
        tl.meta.style = name
    if client_style:
        if client_style == "none":
            tl.meta.client_style = None
        elif not (styles_dir() / "clients" / f"{client_style}.style.yaml").is_file():
            known = ", ".join(p.stem.replace(".style", "") for p in (styles_dir() / "clients").glob("*.style.yaml"))
            raise RefusedError(f"No client style '{client_style}'.", fix=f"Known: {known or 'none'}; "
                               f"styles: {', '.join(list_styles())}.")
        else:
            tl.meta.client_style = client_style
    if not name and not client_style:
        emit(as_json, {"style": tl.meta.style, "client_style": tl.meta.client_style})
        if not as_json:
            out.print(f"style: {tl.meta.style}, client theme: {tl.meta.client_style or tl.meta.client or 'none'}")
        return
    from cutroom.graphics.contrast import theme_problems
    from cutroom.style import timeline_style

    problems = theme_problems(timeline_style(tl).get("graphics", {}).get("theme", {}))
    n = store.save_new(job, tl, command="style",
                       summary=f"style {tl.meta.style}, client theme {tl.meta.client_style or 'none'}")
    for p in problems:
        job.log.warn(f"theme contrast: {p}")
    emit(as_json, {"version": n, "style": tl.meta.style, "client_style": tl.meta.client_style,
                   "contrast_problems": problems})
    if not as_json:
        out.print(f"style {tl.meta.style}, client theme {tl.meta.client_style or 'none'} → v{n:03d}")
        for p in problems:
            out.print(f"[yellow]  ! theme contrast: {p}[/]")


CLIENT_TEMPLATE = """# Client style: {name}. Deep-merged over styles/default.style.yaml (only list what differs).
# Brand values come from the client's brand guide, never guessed. Check contrast after editing:
# `cutroom style <job> --client-style {name}` warns when text or accent colours fail WCAG on panels.
name: {name}
{inherit}captions:
  default_style: clean          # clean | bold-karaoke | word-highlight | minimal
graphics:
  theme:
    colors:
      primary: "#6D28D9"        # brand colour (bars, highlights)
      accent: "#FFD60A"         # emphasis (numbers, active items)
      text: "#FFFFFF"
      surface: "rgba(12,12,18,0.84)"   # panels behind text
    fonts: {{heading: Montserrat, body: Inter}}   # bundled: Inter, Montserrat (OFL)
    weights: {{heading: 800, body: 500, label: 700}}
    radius: 0.012
    motion: {{enter_frames: 12, exit_frames: 8, stagger_frames: 4}}
audio:
  music: {{music_under_speech_db: 12.0, duck_db: 8.0}}
"""


@tool(ToolManifest("client-style", "Create a client style file (brand colours, fonts, captions) from a template.",
                   "timeline", inputs=["styles/default.style.yaml"], outputs=["styles/clients/<name>.style.yaml"],
                   tests=["tests/test_delivery.py"]))
def client_style(name: Annotated[str, typer.Argument(help="Client name (lower-case, e.g. acme).")],
                 from_style: Annotated[Optional[str], typer.Option("--from-style", help="Note a learned style (e.g. from analyze-reference) to use with it.")] = None,
                 as_json: JsonOpt = False) -> None:
    """Writes a commented template; fill it from the client's brand guide. Jobs created with
    `cutroom new <job> --client <name>` use it automatically."""
    import re

    from cutroom.style import styles_dir

    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,40}", name):
        raise RefusedError(f"Bad client name '{name}'.", fix="Lower-case letters, digits, '.', '_' or '-'.")
    p = styles_dir() / "clients" / f"{name}.style.yaml"
    if p.exists():
        raise RefusedError(f"{p.name} exists.", fix="Edit it directly.")
    inherit = (f"# Pacing learned from a reference: assemble jobs with `--style {from_style}`.\n" if from_style else "")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CLIENT_TEMPLATE.format(name=name, inherit=inherit), encoding="utf-8")
    emit(as_json, {"path": str(p)})
    if not as_json:
        out.print(f"Wrote {p} (fill it from the brand guide)")
