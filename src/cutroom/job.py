"""Per-job workspaces: ``jobs/<job-id>/`` with a manifest, log and stage folders."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from cutroom import __version__
from cutroom.errors import JobError
from cutroom.ingest.probe import MediaInfo
from cutroom.joblog import JobLog, now_iso
from cutroom.paths import jobs_dir, resolve_stored

JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$")
SUBDIRS = ["source", "inputs", "proxy", "analysis", "versions", "renders", "review", "delivery", "logs"]

Role = Literal["main", "broll", "music", "sfx", "voiceover", "image", "unknown"]


class SourceRecord(BaseModel):
    id: str = Field(description="Stable source id, e.g. S01. Timelines reference this.")
    name: str = Field(description="Original file name.")
    original_path: str = Field(description="Absolute path the file was ingested from.")
    path: str = Field(description="Where Cutroom reads it: job-relative if copied, else absolute.")
    copied: bool
    hash: str = Field(description="blake2b-128 of the file bytes; used to detect changes and relink.")
    size_bytes: int
    added_at: str
    role: Role = "unknown"
    media: MediaInfo
    proxy: str | None = None
    proxy_params: dict | None = None
    audio: dict[str, str] = Field(default_factory=dict, description="Extracted audio files by purpose.")
    transcript: str | None = None


class StageRecord(BaseModel):
    status: Literal["pending", "running", "done", "failed"] = "pending"
    updated_at: str = Field(default_factory=now_iso)
    info: dict = Field(default_factory=dict)


class JobManifest(BaseModel):
    schema_version: int = 1
    id: str
    client: str | None = None
    title: str | None = None
    created_at: str = Field(default_factory=now_iso)
    cutroom_version: str = __version__
    sources: list[SourceRecord] = Field(default_factory=list)
    stages: dict[str, StageRecord] = Field(default_factory=dict)
    parent: str | None = Field(None, description="For a derived job (a Short): the job it was cut from.")
    derived: dict = Field(default_factory=dict, description="How it was derived (parent version, range, score).")


BRIEF_TEMPLATE = """# Brief: {job_id}

Client: {client}

## Deliverables
- [ ] YouTube long-form (16:9)
- [ ] YouTube Shorts (9:16), how many:

## Goal and audience
(What is this video for? Who watches it?)

## Must keep / must cut
-

## Tone and style
(Reference videos go in references/. Client style: styles/clients/<client>.style.yaml)

## Names, brands and terms to spell correctly
-

## On-screen facts
(Graphics only show numbers and claims from the transcript or from documents you provide.
Put fact sheets, reports and logos in inputs/. Speaker names and titles for lower thirds:)
-

## Deadline
"""


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file + rename so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        # mkstemp creates owner-only (0600) files; give the result normal permissions.
        os.chmod(tmp, 0o666 & ~_umask())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def validate_job_id(job_id: str) -> str:
    if not JOB_ID_RE.match(job_id):
        raise JobError(
            f"Invalid job id '{job_id}'.",
            fix="Use 3-64 lower-case letters, digits, '.', '_' or '-', starting and ending with a "
                "letter or digit, e.g. 'acme-2026-11-launch'.")
    return job_id


class Job:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.manifest_path = self.root / "job.json"
        self.log = JobLog(self.root)
        self._manifest: JobManifest | None = None

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, job_id: str, *, client: str | None = None, title: str | None = None,
               base: Path | None = None) -> "Job":
        validate_job_id(job_id)
        root = (base or jobs_dir()) / job_id
        if (root / "job.json").exists():
            raise JobError(f"Job '{job_id}' already exists at {root}.",
                           fix="Pick another id, or use the existing job.")
        for d in SUBDIRS:
            (root / d).mkdir(parents=True, exist_ok=True)
        job = cls(root)
        job._manifest = JobManifest(id=job_id, client=client, title=title)
        job.save()
        brief = root / "brief.md"
        if not brief.exists():
            atomic_write_text(brief, BRIEF_TEMPLATE.format(job_id=job_id, client=client or "(unknown)"))
        job.log.init(job_id)
        job.log.section("job created")
        job.log.note(f"Workspace {root} (client: {client or 'unspecified'}).")
        return job

    @classmethod
    def load(cls, job: str | Path, *, base: Path | None = None) -> "Job":
        p = Path(job)
        root = p if (p / "job.json").exists() else (base or jobs_dir()) / str(job)
        if not (root / "job.json").exists():
            raise JobError(f"No job '{job}' found (looked for {root / 'job.json'}).",
                           fix="Create it with `cutroom new <job-id>`, or list jobs with `cutroom jobs`.")
        return cls(root)

    @property
    def manifest(self) -> JobManifest:
        if self._manifest is None:
            try:
                self._manifest = JobManifest.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                raise JobError(f"Job manifest {self.manifest_path} is unreadable: {e}",
                               fix="Restore it from git or a backup; do not hand-edit job.json.") from e
        return self._manifest

    def save(self) -> None:
        atomic_write_text(self.manifest_path, self.manifest.model_dump_json(indent=2) + "\n")

    @property
    def id(self) -> str:
        return self.manifest.id

    # ------------------------------------------------------------------ sources
    def next_source_id(self) -> str:
        used = {s.id for s in self.manifest.sources}
        n = 1
        while f"S{n:02d}" in used:
            n += 1
        return f"S{n:02d}"

    def source(self, sid: str) -> SourceRecord:
        for s in self.manifest.sources:
            if s.id.lower() == sid.lower():
                return s
        known = ", ".join(s.id for s in self.manifest.sources) or "none"
        raise JobError(f"Job '{self.id}' has no source '{sid}'.", fix=f"Known sources: {known}.")

    def select_sources(self, ids: list[str] | None) -> list[SourceRecord]:
        if not self.manifest.sources:
            raise JobError(f"Job '{self.id}' has no sources yet.",
                           fix=f"Add footage with `cutroom ingest {self.id} <files>`.")
        return [self.source(i) for i in ids] if ids else list(self.manifest.sources)

    def find_by_hash(self, digest: str) -> SourceRecord | None:
        return next((s for s in self.manifest.sources if s.hash == digest), None)

    def abspath(self, stored: str) -> Path:
        return resolve_stored(stored, self.root)

    def source_path(self, src: SourceRecord) -> Path:
        p = self.abspath(src.path)
        if not p.is_file():
            raise JobError(f"Source {src.id} is missing: {p}",
                           fix="Move the file back, or re-ingest it (Cutroom matches files by hash).")
        return p

    def analysis_dir(self, sid: str) -> Path:
        d = self.root / "analysis" / sid
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------------ stages
    def mark_stage(self, name: str, status: str, **info) -> None:
        self.manifest.stages[name] = StageRecord(status=status, info=info)
        self.save()


def list_jobs(base: Path | None = None) -> list[JobManifest]:
    base = base or jobs_dir()
    out = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            if (p / "job.json").is_file():
                try:
                    out.append(Job(p).manifest)
                except JobError:
                    continue
    return out
