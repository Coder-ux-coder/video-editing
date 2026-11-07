"""Helpers shared by tool plugins: open an edit, apply removals, save a version, report."""

from __future__ import annotations

from dataclasses import dataclass

from cutroom.cli_common import emit, out
from cutroom.job import Job
from cutroom.perception.words import WordsFile
from cutroom.style import timeline_style
from cutroom.timeline import store
from cutroom.timeline.model import CutDecision, Timeline
from cutroom.timeline.ops import Removal, check_cut_points, remove_ranges, words_for
from cutroom.timeline.outline import tc


@dataclass
class EditContext:
    job: Job
    tl: Timeline
    base: int
    words: dict[str, WordsFile | None]
    style: dict
    envs: dict

    def env(self, sid: str):
        """5 ms energy envelope of a source (for valley-snapping cuts), or None."""
        if sid not in self.envs:
            import numpy as np

            p = self.job.root / "analysis" / sid / "envelope.npz"
            self.envs[sid] = np.load(p)["env"] if p.is_file() else None
        return self.envs[sid]

    def shots(self, sid: str) -> list[float]:
        """The source's own camera changes (from the visual report), to hide cuts in them."""
        import json

        p = self.job.root / "analysis" / sid / "visual_report.json"
        if not p.is_file():
            return []
        rep = json.loads(p.read_text(encoding="utf-8"))
        return [sh["start"] for sh in rep.get("shots", [])[1:]]

    @property
    def ed(self) -> dict:
        return self.style["editing"]


def open_edit(job_id: str, version: str | None = None, *, need_words: bool = True) -> EditContext:
    from cutroom.perception.pipeline import ensure_words

    job = Job.load(job_id)
    base = store.resolve(job, version)
    tl = store.load(job, base)
    words: dict[str, WordsFile | None] = {}
    for s in tl.sources:
        if need_words and s.has_audio:
            words[s.id] = ensure_words(job, job.source(s.id))
            s.words = f"analysis/{s.id}/words.json"
        else:
            words[s.id] = words_for(job, s.id)
    style = timeline_style(tl)
    return EditContext(job, tl, base, words, style, {})


def apply_and_save(ctx: EditContext, removals: list[Removal], *, tool: str, title: str,
                   dry_run: bool = False) -> dict:
    """Apply removals to a copy of the timeline, check cut points, save as a new version."""
    tl = ctx.tl.model_copy(deep=True)
    nxt = (max(store.list_versions(ctx.job)) + 1) if store.list_versions(ctx.job) else 1
    applied = remove_ranges(tl, removals, tool=tool, version=nxt, words=ctx.words, min_clip=ctx.ed["min_clip"])
    problems = check_cut_points(tl, ctx.words)
    removed = sum(c.duration for c in applied)
    summary = (f"{title}: {len(applied)} cuts removing {removed:.1f}s; duration "
               f"{tc(ctx.tl.duration)} → {tc(tl.duration)}")
    result = {"base_version": ctx.base, "dry_run": dry_run, "cuts": [c.model_dump() for c in applied],
              "removed_s": round(removed, 3), "duration_before_s": round(ctx.tl.duration, 3),
              "duration_after_s": round(tl.duration, 3), "word_boundary_problems": problems, "summary": summary}
    if dry_run or not applied:
        result["version"] = None
        return result
    ctx.job.log.section(f"{tool} (from v{ctx.base:03d})")
    for c in applied:
        log_cut(ctx.job, c)
    for p in problems:
        ctx.job.log.warn(p)
    result["version"] = store.save_new(ctx.job, tl, command=tool, summary=summary, parent=ctx.base)
    return result


def log_cut(job: Job, c: CutDecision) -> None:
    said = f" \"{c.text}\"" if c.text else ""
    conf = f", confidence {c.confidence:.2f}" if c.confidence is not None else ""
    job.log.note(f"cut {c.id} {c.source} {tc(c.src_in)}–{tc(c.src_out)} ({c.duration:.2f}s) "
                 f"{c.kind}{said}: {c.reason}{conf}")


def report(result: dict, as_json: bool, max_lines: int = 25) -> None:
    if as_json:
        emit(True, result)
        return
    for c in result["cuts"][:max_lines]:
        said = f' "{c["text"]}"' if c["text"] else ""
        out.print(f"  ✂ {c['source']} {tc(c['src_in'])} {c['src_out'] - c['src_in']:.2f}s {c['kind']}{said}: {c['reason']}")
    if len(result["cuts"]) > max_lines:
        out.print(f"  … {len(result['cuts']) - max_lines} more")
    for p in result["word_boundary_problems"]:
        out.print(f"[yellow]  ! {p}[/]")
    tag = "[dim](dry run, not saved)[/]" if result["dry_run"] else (
        f"→ [bold]v{result['version']:03d}[/]" if result["version"] else "(nothing to change)")
    out.print(f"{result['summary']} {tag}")
