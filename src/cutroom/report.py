"""Job report: where the time went (every command, every render stage), what it cost, and what
reached the network.

Everything Cutroom runs is local and free, so the cost is always $0.00; the report still says so
explicitly and lists what the network was used for (model downloads, licensed asset fetches), so a
person can verify it rather than take it on trust.
"""

from __future__ import annotations

import json
from collections import defaultdict

from cutroom.job import Job, atomic_write_text
from cutroom.timeline import store
from cutroom.timeline.outline import tc


def _commands(job: Job) -> list[dict]:
    p = job.log.jsonl_path
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue                                   # a line cut off by a crash; ignore it
    return out


def _renders(job: Job) -> list[dict]:
    out = []
    d = job.root / "renders"
    for js in sorted(d.glob("v*_*.json")) if d.is_dir() else []:
        try:
            r = json.loads(js.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if "elapsed_s" not in r:
            continue
        checks = r.get("checks", {})
        out.append({"file": js.name, "version": r.get("version"), "mode": r.get("mode"), "size": r.get("size"),
                    "range": r.get("range"), "duration_s": r.get("duration_s"), "elapsed_s": r["elapsed_s"],
                    "timings_s": r.get("timings_s", {}), "resumed": bool(r.get("resumed")),
                    "checks_failed": [k for k, v in checks.items() if not v]})
    return out


def _assets_used(job: Job) -> list[dict]:
    from cutroom.assets.library import list_assets

    if not (job.root / "versions").is_dir():
        return []
    tl = store.load(job)
    ids = {b.asset for b in tl.broll} | {b.asset for b in tl.audio.music} | {b.asset for b in tl.audio.sfx}
    lib = {a.id: a for a in list_assets()}
    out = []
    for i in sorted(ids):
        a = lib.get(i)
        out.append({"id": i, "license": a.license if a else "?", "source": (a.source_url or "generated locally") if a else "?",
                    "author": a.author if a else ""})
    return out


def _models() -> list[dict]:
    """Models downloaded on this machine (pinned revisions, free licences)."""
    from cutroom.perception import models as M

    out = [{"name": m.name, "license": m.licence, "source": f"huggingface.co/{m.repo}@{m.revision[:8]}"}
           for m in M.ASR_MODELS.values() if M.is_downloaded(m.name)]
    for m in M.VISION_MODELS.values():
        if (M.models_dir() / "vision" / m.filename).is_file():
            out.append({"name": m.filename, "license": m.licence, "source": m.url.split("/resolve/")[0]})
    return out


def job_report(job: Job) -> dict:
    from cutroom.tools.delivery_tools import job_state

    cmds = _commands(job)
    by = defaultdict(lambda: {"runs": 0, "ok": 0, "failed": 0, "seconds": 0.0})
    for c in cmds:
        b = by[c["command"]]
        b["runs"] += 1
        b["ok" if c.get("status") == "ok" else "failed"] += 1
        b["seconds"] = round(b["seconds"] + float(c.get("elapsed_s", 0.0)), 1)
    renders = _renders(job)
    stages = defaultdict(float)
    for r in renders:
        for k, v in r["timings_s"].items():
            stages[k] = round(stages[k] + v, 1)
    head = store.head(job) if (job.root / "versions").is_dir() and any((job.root / "versions").glob("v*.json")) else None
    tl = store.load(job, head) if head else None
    rv = job.root / "review" / f"v{head:03d}" / "review.json" if head else None
    review = json.loads(rv.read_text(encoding="utf-8"))["scorecard"] if rv and rv.is_file() else None
    return {
        "job": job.id, "client": job.manifest.client, "head": head,
        "duration_s": round(tl.duration, 2) if tl else None,
        "sources_s": round(sum(s.media.duration for s in job.manifest.sources), 2),
        "stages": job_state(job),
        "time": {"commands": dict(sorted(by.items(), key=lambda kv: -kv[1]["seconds"])),
                 "total_s": round(sum(float(c.get("elapsed_s", 0.0)) for c in cmds), 1),
                 "failed_commands": sum(1 for c in cmds if c.get("status") != "ok"),
                 "render_stages_s": dict(stages)},
        "renders": renders,
        "review": review,
        "cost": {"usd": 0.0, "paid_services": [],
                 "note": "Every tool, model and asset is free; nothing is billed per use."},
        "network": {"client_media_uploaded": False,
                    "note": "Cutroom has no upload code. The network is used only to download free models "
                            "(perception/models.py) and free-licensed assets (assets/library.py).",
                    "models_installed": _models(), "assets_in_edit": _assets_used(job)},
    }


def report_md(r: dict) -> str:
    def hms(s: float) -> str:
        s = int(round(s))
        return f"{s // 3600}h{s % 3600 // 60:02d}m{s % 60:02d}s" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"

    lines = [f"# Job report: {r['job']}", "",
             f"Client: {r['client'] or '(none)'} · HEAD v{r['head']:03d} · edit {tc(r['duration_s'])} from "
             f"{tc(r['sources_s'])} of footage" if r["head"] else f"Client: {r['client'] or '(none)'} · no timeline yet",
             "", "## Stages", ""]
    lines += [f"- [{'x' if s['done'] else ' '}] {s['stage']}" + ("" if s["done"] else f": `{s['next']}`")
              + (f" ({s['note']})" if s["note"] else "") for s in r["stages"]]
    t = r["time"]
    lines += ["", "## Time", "", f"Commands: {hms(t['total_s'])} in total"
              + (f", {t['failed_commands']} failed" if t["failed_commands"] else "") + ".", "",
              "| command | runs | failed | time |", "|---|---:|---:|---:|"]
    lines += [f"| {k} | {v['runs']} | {v['failed']} | {hms(v['seconds'])} |" for k, v in list(t["commands"].items())[:20]]
    if t["render_stages_s"]:
        lines += ["", "Render stages (all renders): " + ", ".join(f"{k} {hms(v)}" for k, v in t["render_stages_s"].items())]
    full = [x for x in r["renders"] if not x["range"]]
    if full:
        lines += ["", "| render | size | length | took | stages | checks |", "|---|---|---:|---:|---|---|"]
        for x in full[-10:]:
            st = ", ".join(f"{k} {v:.0f}s" for k, v in x["timings_s"].items()) or "(not recorded)"
            ck = "all pass" if not x["checks_failed"] else "FAILED: " + ", ".join(x["checks_failed"])
            lines.append(f"| {x['file'].removesuffix('.json')}{' (resumed)' if x['resumed'] else ''} | {x['size']} | "
                         f"{tc(x['duration_s'] or 0, ms=False)} | {hms(x['elapsed_s'])} | {st} | {ck} |")
    if r["review"]:
        c = r["review"]["counts"]
        lines += ["", f"Review of HEAD: {c['blocker']} blocker, {c['major']} major, {c['minor']} minor, "
                      f"{c['polish']} polish; overall {r['review']['overall']}/10."]
    lines += ["", "## Cost", "", f"**${r['cost']['usd']:.2f}.** Paid services used: none. {r['cost']['note']}", "",
              "## Network", "", r["network"]["note"], "", "Client footage uploaded anywhere: **no**.", ""]
    if r["network"]["models_installed"]:
        lines += ["Models on this machine:"] + [f"- {m['name']} ({m['license']}) from {m['source']}"
                                                for m in r["network"]["models_installed"]] + [""]
    if r["network"]["assets_in_edit"]:
        lines += ["Assets in the edit:"] + [f"- {a['id']}: {a['license']}, {a['source']}" for a in
                                           r["network"]["assets_in_edit"]] + [""]
    return "\n".join(lines)


def write_report(job: Job) -> tuple[dict, str]:
    r = job_report(job)
    md = report_md(r)
    (job.root / "logs").mkdir(exist_ok=True)
    atomic_write_text(job.root / "logs" / "report.json", json.dumps(r, indent=1, default=str) + "\n")
    atomic_write_text(job.root / "logs" / "report.md", md + "\n")
    return r, md
