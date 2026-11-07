"""Register footage in a job. Never modifies the original files."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cutroom.errors import CutroomError, MediaError
from cutroom.ingest.probe import MEDIA_EXTS, probe
from cutroom.job import Job, Role, SourceRecord
from cutroom.joblog import now_iso
from cutroom.paths import rel_or_abs

CHUNK = 8 * 1024 * 1024


def _hasher():
    return hashlib.blake2b(digest_size=16)


def hash_file(path: Path, on_bytes: Callable[[int], None] | None = None) -> str:
    h = _hasher()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
            if on_bytes:
                on_bytes(len(chunk))
    return "blake2b:" + h.hexdigest()


def copy_and_hash(src: Path, dst: Path, on_bytes: Callable[[int], None] | None = None) -> str:
    """Copy ``src`` to ``dst`` and hash it in a single read pass."""
    h = _hasher()
    tmp = dst.with_name(dst.name + ".partial")
    try:
        with src.open("rb") as fi, tmp.open("wb") as fo:
            while chunk := fi.read(CHUNK):
                h.update(chunk)
                fo.write(chunk)
                if on_bytes:
                    on_bytes(len(chunk))
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    st = src.stat()
    os.utime(dst, (st.st_atime, st.st_mtime))
    # Our own copy is read-only, so nothing in the pipeline can alter it by accident.
    dst.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    return "blake2b:" + h.hexdigest()


def safe_name(name: str, max_len: int = 80) -> str:
    stem, ext = os.path.splitext(name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "file"
    return stem[: max_len - len(ext)] + ext.lower()


def expand_inputs(paths: list[Path]) -> list[Path]:
    """Files as given; folders expand to the media files inside them (recursive, sorted)."""
    out: list[Path] = []
    for p in paths:
        p = Path(p).expanduser()
        if p.is_dir():
            out += sorted(f for f in p.rglob("*")
                          if f.is_file() and f.suffix.lower() in MEDIA_EXTS and not f.name.startswith("."))
        else:
            out.append(p)
    return out


@dataclass
class IngestResult:
    path: str
    status: str               # "added" | "duplicate" | "failed"
    source_id: str | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _infer_role(kind: str) -> Role:
    return "image" if kind == "image" else "unknown"


def ingest(job: Job, paths: list[Path], *, copy: bool = True, role: Role | None = None,
           on_bytes: Callable[[int], None] | None = None) -> list[IngestResult]:
    """Probe, hash and register each file. Bad files are reported, not fatal."""
    files = expand_inputs(paths)
    if not files:
        # Footage dropped straight into source/ by the user.
        files = sorted(f for f in (job.root / "source").glob("*")
                       if f.is_file() and f.suffix.lower() in MEDIA_EXTS)
    if not files:
        raise MediaError("No media files to ingest.",
                         fix=f"Pass file or folder paths, or copy footage into {job.root / 'source'}.")
    results: list[IngestResult] = []
    job.log.section(f"ingest ({len(files)} file(s), {'copy' if copy else 'reference in place'})")
    for f in files:
        try:
            results.append(_ingest_one(job, f, copy=copy, role=role, on_bytes=on_bytes))
        except CutroomError as e:
            job.log.warn(f"Could not ingest `{f}`: {e.message}")
            results.append(IngestResult(str(f), "failed", message=e.message + (f" Fix: {e.fix}" if e.fix else "")))
    job.save()
    return results


def _ingest_one(job: Job, f: Path, *, copy: bool, role: Role | None,
                on_bytes: Callable[[int], None] | None) -> IngestResult:
    f = f.resolve()
    info = probe(f)  # raises MediaError for unreadable media, before any copying
    sid = job.next_source_id()
    source_dir = (job.root / "source").resolve()
    inside_job = source_dir in f.parents
    if copy and not inside_job:
        dst = source_dir / f"{sid}_{safe_name(f.name)}"
        digest = copy_and_hash(f, dst, on_bytes)
        dup = job.find_by_hash(digest)
        if dup:
            dst.chmod(stat.S_IWRITE | stat.S_IREAD)
            dst.unlink()
            return IngestResult(str(f), "duplicate", dup.id, f"identical to {dup.id} ({dup.name})")
        stored, copied = rel_or_abs(dst, job.root), True
    else:
        digest = hash_file(f, on_bytes)
        dup = job.find_by_hash(digest)
        if dup:
            return IngestResult(str(f), "duplicate", dup.id, f"identical to {dup.id} ({dup.name})")
        stored, copied = rel_or_abs(f, job.root), inside_job
    rec = SourceRecord(id=sid, name=f.name, original_path=str(f), path=stored, copied=copied,
                       hash=digest, size_bytes=info.size_bytes, added_at=now_iso(),
                       role=role or _infer_role(info.kind), media=info)
    job.manifest.sources.append(rec)
    job.save()
    job.log.note(f"{sid} ← `{f.name}`: {info.summary()} ({'copied to ' + stored if copied and not inside_job else 'read in place'}), {digest[:23]}…")
    v = info.primary_video
    if v and v.likely_vfr:
        job.log.warn(f"{sid} looks variable-frame-rate (nominal {v.fps}, average {v.avg_fps}). "
                     "Proxies and renders conform it to constant frame rate to keep audio in sync.")
    if v and v.is_hdr:
        job.log.warn(f"{sid} is HDR ({v.color_transfer}); proxies are tone-mapped to SDR Rec.709.")
    return IngestResult(str(f), "added", sid, info.summary())
