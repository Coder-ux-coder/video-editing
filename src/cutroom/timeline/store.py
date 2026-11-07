"""Immutable timeline versions: ``versions/v001.json`` … plus ``HEAD`` and ``CHANGELOG.md``.

A version is never rewritten. Undo moves HEAD to the parent; the next edit after an undo
branches from there (the abandoned versions stay on disk and in the changelog).
"""

from __future__ import annotations

import re
from pathlib import Path

from cutroom.errors import JobError, RefusedError
from cutroom.job import Job, atomic_write_text
from cutroom.joblog import now_iso
from cutroom.timeline.model import ChangeEntry, Timeline, validate_timeline


def versions_dir(job: Job) -> Path:
    d = job.root / "versions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def version_path(job: Job, n: int) -> Path:
    return versions_dir(job) / f"v{n:03d}.json"


def list_versions(job: Job) -> list[int]:
    return sorted(int(m.group(1)) for p in versions_dir(job).glob("v*.json")
                  if (m := re.fullmatch(r"v(\d{3,})\.json", p.name)))


def head(job: Job) -> int | None:
    p = versions_dir(job) / "HEAD"
    if p.is_file():
        txt = p.read_text(encoding="utf-8").strip()
        if txt.isdigit():
            return int(txt)
    vs = list_versions(job)
    return vs[-1] if vs else None


def _set_head(job: Job, n: int) -> None:
    atomic_write_text(versions_dir(job) / "HEAD", f"{n}\n")


def resolve(job: Job, ref: str | int | None) -> int:
    """'v003' | '3' | 3 | 'HEAD' | 'HEAD~1' | None (HEAD) → version number."""
    h = head(job)
    if h is None:
        raise JobError(f"Job '{job.id}' has no timeline yet.",
                       fix=f"Create one with `cutroom assemble {job.id}` or `cutroom rough-cut {job.id}`.")
    if ref is None or str(ref).upper() == "HEAD":
        return h
    s = str(ref).strip()
    m = re.fullmatch(r"(?i)HEAD~(\d+)", s)
    if m:
        n = h
        for _ in range(int(m.group(1))):
            parent = load(job, n).meta.parent
            if parent is None:
                raise JobError(f"{s} goes past the first version.", fix="Use a smaller number.")
            n = parent
        return n
    m = re.fullmatch(r"(?i)v?(\d+)", s)
    if not m or int(m.group(1)) not in list_versions(job):
        raise JobError(f"Unknown version '{ref}'.", fix=f"Versions: {', '.join(f'v{v:03d}' for v in list_versions(job))}")
    return int(m.group(1))


def load(job: Job, ref: str | int | None = None) -> Timeline:
    n = resolve(job, ref) if not isinstance(ref, int) else ref
    path = version_path(job, n)
    try:
        return Timeline.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise JobError(f"Version v{n:03d} is missing ({path}).", fix="Restore it from backup or `cutroom versions`.") from None
    except Exception as e:  # noqa: BLE001
        raise JobError(f"Version v{n:03d} is invalid: {e}", fix="Do not hand-edit version files; use cutroom commands.") from e


def save_new(job: Job, tl: Timeline, *, command: str, summary: str, parent: int | None = None) -> int:
    """Write ``tl`` as the next version (validated, immutable) and move HEAD to it."""
    problems = validate_timeline(tl)
    if problems:
        raise RefusedError("Refusing to save an invalid timeline: " + "; ".join(problems[:5]),
                           fix="This is a bug in the command that produced it; please report the message.")
    existing = list_versions(job)
    n = (existing[-1] + 1) if existing else 1
    parent = head(job) if parent is None else parent
    tl = tl.model_copy(deep=True)
    tl.meta.version = n
    tl.meta.parent = parent
    tl.meta.created_at = now_iso()
    tl.meta.changelog = [*tl.meta.changelog, ChangeEntry(version=n, command=command, summary=summary)]
    path = version_path(job, n)
    if path.exists():
        raise RefusedError(f"{path.name} already exists; versions are immutable.", fix="Retry the command.")
    atomic_write_text(path, tl.model_dump_json(indent=1, by_alias=True) + "\n")
    _set_head(job, n)
    log = versions_dir(job) / "CHANGELOG.md"
    if not log.exists():
        atomic_write_text(log, f"# Timeline changelog: {job.id}\n\n")
    with log.open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"- **v{n:03d}** (from {f'v{parent:03d}' if parent else 'nothing'}, {command}): {summary}\n")
    job.log.note(f"Timeline **v{n:03d}** ({command}): {summary}")
    job.mark_stage("timeline", "done", head=n)
    return n


def checkout(job: Job, ref: str | int) -> int:
    n = resolve(job, ref)
    _set_head(job, n)
    job.log.note(f"HEAD moved to v{n:03d}.")
    return n


def undo(job: Job) -> int:
    h = resolve(job, None)
    parent = load(job, h).meta.parent
    if parent is None:
        raise RefusedError(f"v{h:03d} is the first version; nothing to undo.", fix="Nothing to do.")
    _set_head(job, parent)
    job.log.note(f"Undo: HEAD moved from v{h:03d} back to v{parent:03d} (v{h:03d} kept on disk).")
    return parent
