"""Delivery package and the human approval gate.

``prepare`` assembles everything the client needs for one timeline version, after checking that
it is deliverable (final render with every self-check passing; review without blockers or majors,
accepted issues listed; publishing text checked; thumbnail picked; every Short the same). The
package is marked AWAITING APPROVAL. ``approve`` records a person's approval: Cutroom never
approves its own work, and the playbook forbids the agent from running it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from cutroom.errors import RefusedError
from cutroom.job import Job, atomic_write_text, now_iso
from cutroom.timeline import store


def _link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def readiness(job: Job, version: int) -> tuple[list[str], dict]:
    """What blocks delivery of ``version`` (empty = deliverable) and the evidence found."""
    problems, ev = [], {}
    fin = job.root / "renders" / f"v{version:03d}_final.json"
    if not fin.is_file():
        problems.append(f"no final render of v{version:03d} (`cutroom render {job.id} --mode final`)")
    else:
        rep = json.loads(fin.read_text(encoding="utf-8"))
        failed = [k for k, v in rep["checks"].items() if not v]
        if failed:
            problems.append(f"final render checks failed: {', '.join(failed)}")
        ev["final"] = rep
    rv = job.root / "review" / f"v{version:03d}" / "review.json"
    if not rv.is_file():
        problems.append(f"v{version:03d} has not been reviewed (`cutroom review {job.id}`)")
    else:
        r = json.loads(rv.read_text(encoding="utf-8"))
        if not r["scorecard"]["ready"]:
            problems.append(f"review of v{version:03d}: {r['scorecard']['counts']['blocker']} blocker, "
                            f"{r['scorecard']['counts']['major']} major")
        ev["review"] = r
    if job.manifest.parent is None:
        if not (job.root / "renders" / "thumbnail.jpg").is_file():
            problems.append(f"no thumbnail picked (`cutroom thumbnails {job.id} --text … --pick N`)")
        from cutroom import publish as P

        res = P.check(job)
        if not res["ok"]:
            problems += [f"publish.json: {p}" for p in res["problems"]]
        ev["publish"] = res
    else:
        from cutroom.shorts.publish import check_publish

        res = check_publish(job)
        if not res["ok"]:
            problems += [f"publish.json: {p}" for p in res["problems"]]
        ev["publish"] = res
    return problems, ev


def shorts_of(job: Job) -> list[Job]:
    from cutroom.tools.analysis_tools import _derived_jobs

    return _derived_jobs(job)


def prepare(job: Job, version: int | None = None) -> Path:
    n = version or store.head(job)
    problems, ev = readiness(job, n)
    shorts = []
    for sj in shorts_of(job):
        sn = store.head(sj)
        p2, e2 = readiness(sj, sn)
        if p2:
            problems += [f"{sj.id}: {p}" for p in p2]
        shorts.append((sj, sn, e2))
    if problems:
        raise RefusedError("Not deliverable yet:\n  - " + "\n  - ".join(problems),
                           fix="Fix each item (playbooks 06–09), then run deliver again.")
    tl = store.load(job, n)
    pkg = job.root / "delivery" / f"{job.id}_v{n:03d}"
    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)
    files = {}
    _link(job.root / ev["final"]["path"], pkg / f"{job.id}.mp4")
    files["video"] = f"{job.id}.mp4"
    _link(job.root / "renders" / "thumbnail.jpg", pkg / "thumbnail.jpg")
    pub = ev["publish"]
    atomic_write_text(pkg / "title.txt", pub["title"] + "\n")
    atomic_write_text(pkg / "description.txt", pub["description"])
    if tl.captions and tl.captions.cues:
        from cutroom.captions.export import to_srt, to_vtt

        atomic_write_text(pkg / f"{job.id}.srt", to_srt(tl.captions))
        atomic_write_text(pkg / f"{job.id}.vtt", to_vtt(tl.captions))
    from cutroom.export.nle import export_all

    nle = export_all(job, tl, pkg / "nle")
    for sj, sn, e2 in shorts:
        sd = pkg / "shorts" / sj.id.split(".")[-1]
        _link(sj.root / e2["final"]["path"], sd / f"{sj.id}.mp4")
        stl = store.load(sj, sn)
        if stl.captions and stl.captions.cues:
            from cutroom.captions.export import to_srt

            atomic_write_text(sd / f"{sj.id}.srt", to_srt(stl.captions))
        shutil.copy2(sj.root / "publish.json", sd / "publish.json")
    manifest = {"job": job.id, "client": job.manifest.client, "version": n, "prepared_at": now_iso(),
                "status": "AWAITING APPROVAL", "video": ev["final"]["size"], "duration_s": ev["final"]["duration_s"],
                "loudness": ev["final"]["loudness"], "checks": ev["final"]["checks"],
                "review": ev["review"]["scorecard"], "accepted_issues": ev["review"].get("accepted", []),
                "shorts": [{"job": sj.id, "version": sn, "duration_s": e2["final"]["duration_s"],
                            "title": e2["publish"].get("title")} for sj, sn, e2 in shorts],
                "nle": {k: (v["path"] if isinstance(v, dict) else v) for k, v in nle.items()}}
    atomic_write_text(pkg / "MANIFEST.json", json.dumps(manifest, indent=1, default=str) + "\n")
    atomic_write_text(pkg / "REPORT.md", report_md(job, tl, manifest, ev))
    atomic_write_text(pkg / "APPROVAL.md", approval_md(job, n, manifest))
    sums = []
    for p in sorted(pkg.rglob("*")):
        if p.is_file() and p.name != "checksums.sha256":
            sums.append(f"{_sha256(p)}  {p.relative_to(pkg).as_posix()}")
    atomic_write_text(pkg / "checksums.sha256", "\n".join(sums) + "\n")
    job.log.section("delivery prepared")
    job.log.note(f"{pkg.relative_to(job.root)}: AWAITING APPROVAL ({len(sums)} files)")
    return pkg


def report_md(job: Job, tl, m: dict, ev: dict) -> str:
    from cutroom.publish import credits

    acc = "\n".join(f"- {a['title']} ({a['check']}): {a['accepted_because']}" for a in m["accepted_issues"]) or "- none"
    lines = [f"# Delivery: {job.id} v{m['version']:03d}", "", f"Status: **{m['status']}**", "",
             "## What is in this package", "",
             f"- `{job.id}.mp4`: {m['video']}, {m['duration_s']:.1f} s, {m['loudness']['integrated_lufs']} LUFS, "
             f"{m['loudness']['true_peak_dbtp']} dBTP", "- `thumbnail.jpg`, `title.txt`, `description.txt` (chapters and credits)",
             f"- `{job.id}.srt` / `.vtt` captions", "- `nle/`: OTIO (Resolve), FCPXML 1.10 (Resolve, Final Cut), EDL (Premiere/Avid), SRT",
             f"- `shorts/`: {len(m['shorts'])} Short(s) with captions and publishing text", "- `checksums.sha256`", "",
             "## Checks on the final render", ""]
    lines += [f"- {k}: {'pass' if v else 'FAIL'}" for k, v in m["checks"].items()]
    lines += ["", f"## Review scorecard (overall {m['review']['overall']}/10)", "",
              " | ".join(f"{k} {v}" for k, v in m["review"]["categories"].items()), "",
              "Accepted by the editor (with reasons):", "", acc, "",
              "## Sources and licences", ""]
    lines += [f"- {c}" for c in credits(job, tl)] or ["- (no third-party assets)"]
    lines += ["", "## Not judged automatically", "",
              "Story choices, taste, factual correctness of what is said, sensitive content: please watch it."]
    return "\n".join(lines) + "\n"


def approval_md(job: Job, n: int, m: dict) -> str:
    return (f"# Approval needed: {job.id} v{n:03d}\n\nWatch `{job.id}.mp4` and the Shorts in full, read `title.txt` and "
            f"`description.txt`, look at `thumbnail.jpg`. If it can go out:\n\n    cutroom approve {job.id} "
            f"--package {job.id}_v{n:03d} --by \"Your Name\"\n\nIf not, write your notes (one per line, with times as "
            f"you saw them) and give them to the editor: `cutroom revise {job.id} --notes notes.txt --seen v{n:03d}`.\n")


def approve(job: Job, package: str, by: str, notes: str = "") -> dict:
    pkg = job.root / "delivery" / package
    if not (pkg / "MANIFEST.json").is_file():
        raise RefusedError(f"No delivery package '{package}'.", fix="See the delivery/ folder.")
    if not by.strip():
        raise RefusedError("Approval needs the approver's name.", fix="--by \"Your Name\"")
    m = json.loads((pkg / "MANIFEST.json").read_text(encoding="utf-8"))
    m["status"] = "APPROVED"
    m["approved"] = {"by": by, "at": now_iso(), "notes": notes}
    atomic_write_text(pkg / "MANIFEST.json", json.dumps(m, indent=1, default=str) + "\n")
    atomic_write_text(pkg / "APPROVAL.md", f"# Approved\n\n{job.id} v{m['version']:03d} approved by {by} at "
                                          f"{m['approved']['at']}.\n" + (f"\n{notes}\n" if notes else ""))
    job.log.section("approved")
    job.log.note(f"{package} approved by {by}")
    return m
