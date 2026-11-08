"""Client revision notes → traceable edits.

1. ``prepare``: each note (a line or bullet of the client's text) gets its timecode mapped from the
   version the client watched to the current edit (through the source: cuts made since are
   accounted for) and the context there (what is said, which graphics and b-roll are on screen).
   Writes ``review/revisions/rNN/task.md`` and a ``plan.json`` to fill in.
2. The agent fills ``plan.json``: for every note, cutroom commands (``<job>`` stands for the job)
   and/or a response (done / question for the client / declined with the reason).
3. ``apply``: runs the commands in order, each as a normal versioned edit, and writes
   ``result.json`` and ``report.md``: note → what was done → version. Nothing is skipped silently.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

from cutroom.errors import RefusedError
from cutroom.job import Job, atomic_write_text
from cutroom.timeline import store
from cutroom.timeline.model import Timeline
from cutroom.timeline.ops import source_to_timeline
from cutroom.timeline.outline import tc

TC = re.compile(r"\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\.(\d+))?\b")
FORBIDDEN = {"approve", "deliver"}           # the human gate is never part of a revision plan


BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def parse_notes(text: str) -> list[dict]:
    """One note per bullet (or per line when there are no bullets); headers are skipped."""
    lines = text.splitlines()
    if any(BULLET.match(ln) for ln in lines):
        lines = [ln for ln in lines if BULLET.match(ln)]
    notes = []
    for line in lines:
        s = BULLET.sub("", line).strip()
        if not s or s.startswith("#"):
            continue
        times = []
        for m in TC.finditer(s):
            h, mi, se, fr = m.groups()
            times.append((int(h or 0) * 3600 + int(mi) * 60 + int(se) + (float("0." + fr) if fr else 0.0)))
        notes.append({"n": len(notes) + 1, "text": s, "times_seen": times})
    return notes


def map_time(seen: Timeline, now: Timeline, t: float) -> dict:
    """A moment in the version the client saw → the same content in the current edit."""
    for c in seen.track_clips():
        if c.start <= t < c.end:
            src = c.to_source(t)
            at = source_to_timeline(now, c.source, src)
            return {"seen": round(t, 2), "source": c.source, "src_t": round(src, 2),
                    "now": round(at, 2) if at is not None else None}
    return {"seen": round(t, 2), "now": None}


def context(job: Job, tl: Timeline, t: float, span: float = 4.0) -> dict:
    from cutroom.captions.plan import timeline_words
    from cutroom.timeline.ops import words_for

    words = {s.id: words_for(job, s.id) for s in tl.sources}
    said = " ".join(w.text for w in timeline_words(tl, words, set()) if t - span <= w.start <= t + span)
    on = [f"{g.id} {g.template} {json.dumps(g.params)[:60]}" for g in tl.graphics if g.start <= t < g.end]
    on += [f"{b.id} b-roll {b.asset}" for b in tl.broll if b.start <= t < b.end]
    clip = next((c.id for c in tl.track_clips() if c.start <= t < c.end), None)
    return {"said": said, "on_screen": on, "clip": clip}


def _rounds(job: Job) -> Path:
    return job.root / "review" / "revisions"


def prepare(job: Job, notes_text: str, seen_version: int | None) -> Path:
    now_n = store.head(job)
    now = store.load(job, now_n)
    seen = store.load(job, seen_version) if seen_version else now
    k = len([p for p in _rounds(job).glob("r*") if p.is_dir()]) + 1 if _rounds(job).is_dir() else 1
    rd = _rounds(job) / f"r{k:02d}"
    rd.mkdir(parents=True, exist_ok=True)
    notes = parse_notes(notes_text)
    if not notes:
        raise RefusedError("No notes found.", fix="One note per line or bullet.")
    for n in notes:
        n["at"] = [map_time(seen, now, t) for t in n["times_seen"]]
        n["context"] = [context(job, now, a["now"]) for a in n["at"] if a["now"] is not None]
        n["commands"] = []
        n["response"] = ""
    atomic_write_text(rd / "notes.txt", notes_text)
    atomic_write_text(rd / "plan.json", json.dumps({"round": k, "seen_version": seen_version or now_n,
                                                    "base_version": now_n, "notes": notes}, indent=1) + "\n")
    lines = [f"# Revision round r{k:02d} for {job.id}", "",
             f"The client watched v{(seen_version or now_n):03d}; the edit is now v{now_n:03d}. Times below are "
             "mapped to the current edit.", "",
             f"Fill `plan.json`: for each note, `commands` (cutroom commands, `<job>` = this job) and/or a "
             f"`response` (done / question for the client / declined and why). Then run "
             f"`cutroom revise {job.id} --apply r{k:02d}`, render and review.", ""]
    for n in notes:
        lines.append(f"## Note {n['n']}: {n['text']}")
        for a, cx in zip([a for a in n["at"] if a["now"] is not None], n["context"]):
            lines.append(f"- seen {tc(a['seen'])} → now {tc(a['now'])} (clip {cx['clip']}): “{cx['said'][:200]}”")
            if cx["on_screen"]:
                lines.append(f"  on screen: {'; '.join(cx['on_screen'])}")
        for a in n["at"]:
            if a["now"] is None:
                lines.append(f"- seen {tc(a['seen'])}: that moment is no longer in the edit")
        lines.append("")
    atomic_write_text(rd / "task.md", "\n".join(lines))
    return rd


def apply(job: Job, round_id: str, *, runner=None) -> dict:
    rd = _rounds(job) / round_id
    plan = json.loads((rd / "plan.json").read_text(encoding="utf-8"))
    missing = [n["n"] for n in plan["notes"] if not n.get("commands") and not n.get("response")]
    if missing:
        raise RefusedError(f"Notes without a plan: {missing}.", fix="Every note needs commands or a response in plan.json.")
    for n in plan["notes"]:
        for cmd in n.get("commands", []):
            argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
            if argv and argv[0] == "cutroom":
                argv = argv[1:]
            if not argv or argv[0] in FORBIDDEN:
                raise RefusedError(f"Note {n['n']}: '{cmd}' cannot be part of a revision plan.",
                                   fix="Approval and delivery are the human's step.")
    runner = runner or _run
    results = []
    for n in plan["notes"]:
        done = []
        for cmd in n.get("commands", []):
            argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
            argv = [a.replace("<job>", job.id) for a in (argv[1:] if argv[0] == "cutroom" else argv)]
            before = store.head(job)
            ok, output = runner(argv)
            done.append({"command": "cutroom " + " ".join(argv), "ok": ok, "version_before": before,
                         "version_after": store.head(job), "output": output[-600:]})
            if not ok:
                break
        results.append({"n": n["n"], "note": n["text"], "response": n.get("response", ""), "done": done,
                        "ok": all(d["ok"] for d in done)})
    res = {"round": round_id, "base_version": plan["base_version"], "final_version": store.head(job), "notes": results}
    atomic_write_text(rd / "result.json", json.dumps(res, indent=1) + "\n")
    lines = [f"# Revision {round_id}: v{plan['base_version']:03d} → v{store.head(job):03d}", ""]
    for r in results:
        status = "done" if r["done"] and r["ok"] else ("FAILED" if r["done"] else "answered")
        lines.append(f"- **Note {r['n']}** ({status}): {r['note']}")
        for d in r["done"]:
            lines.append(f"  - `{d['command']}` → v{d['version_after']:03d}" + ("" if d["ok"] else f" (failed: {d['output'][-200:]})"))
        if r["response"]:
            lines.append(f"  - {r['response']}")
    atomic_write_text(rd / "report.md", "\n".join(lines) + "\n")
    job.log.section(f"revision {round_id}")
    job.log.note(f"applied {sum(len(r['done']) for r in results)} command(s) for {len(results)} note(s) → v{store.head(job):03d}")
    return res


def _run(argv: list[str]) -> tuple[bool, str]:
    p = subprocess.run([sys.executable, "-m", "cutroom", *argv], capture_output=True, text=True)
    return p.returncode == 0, (p.stdout + p.stderr)
