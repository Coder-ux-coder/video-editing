"""Batch queue: a plain text file of cutroom commands, run one after another, resumable.

    # overnight.txt: one command per line ("cutroom" optional), comments with #
    render interview-a --mode final
    shorts interview-a --create 3
    render interview-b --mode final

``run_batch`` runs each line as its own ``cutroom`` process (so one job's crash cannot corrupt
another), records the outcome of every line next to the file (``<file>.state.json``) and, when run
again, skips the lines that already succeeded: an interrupted night resumes where it stopped, and
renders resume their own finished parts. The human gate never runs from a batch.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from cutroom.errors import RefusedError
from cutroom.job import atomic_write_text
from cutroom.joblog import now_iso

FORBIDDEN = {"approve", "batch"}               # a person approves; no nested queues


def parse_batch(text: str) -> list[dict]:
    items, seen = [], {}
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        argv = shlex.split(s, comments=True)
        if argv and argv[0] == "cutroom":
            argv = argv[1:]
        if not argv:
            continue
        if argv[0] in FORBIDDEN:
            raise RefusedError(f"Line {n}: '{argv[0]}' cannot run from a batch.",
                               fix="Approval is a person's step; run other batches one after another.")
        # Keyed by the command and its occurrence, not the line number: adding a line above
        # does not make finished work run again.
        k = seen[shlex.join(argv)] = seen.get(shlex.join(argv), 0) + 1
        key = hashlib.sha1(f"{shlex.join(argv)}#{k}".encode()).hexdigest()[:12]
        items.append({"line": n, "argv": argv, "key": key})
    if not items:
        raise RefusedError("The batch file has no commands.", fix="One cutroom command per line.")
    return items


def state_path(batch_file: Path) -> Path:
    return batch_file.with_name(batch_file.name + ".state.json")


def _run(argv: list[str]) -> tuple[int, str]:
    p = subprocess.run([sys.executable, "-m", "cutroom", *argv], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def run_batch(batch_file: Path, *, keep_going: bool = False, again: bool = False, dry_run: bool = False,
              runner: Callable[[list[str]], tuple[int, str]] | None = None,
              log: Callable[[str], None] = lambda s: None) -> dict:
    items = parse_batch(batch_file.read_text(encoding="utf-8"))
    sp = state_path(batch_file)
    state = {} if again or not sp.is_file() else json.loads(sp.read_text(encoding="utf-8"))
    done = state.setdefault("done", {})
    runner = runner or _run
    results = []
    stopped = False
    for it in items:
        cmd = "cutroom " + shlex.join(it["argv"])
        if it["key"] in done:
            results.append({**it, "command": cmd, "status": "skipped (done earlier)", **done[it["key"]]})
            continue
        if stopped or dry_run:
            results.append({**it, "command": cmd, "status": "not run" if stopped else "would run"})
            continue
        log(f"line {it['line']}: {cmd}")
        t0 = time.perf_counter()
        code, output = runner(it["argv"])
        rec = {"at": now_iso(), "exit": code, "seconds": round(time.perf_counter() - t0, 1),
               "tail": output[-800:]}
        ok = code == 0
        results.append({**it, "command": cmd, "status": "ok" if ok else "FAILED", **rec})
        if ok:
            done[it["key"]] = {k: rec[k] for k in ("at", "seconds")}
        else:
            state.setdefault("failures", []).append({"line": it["line"], "command": cmd, **rec})
            stopped = not keep_going
        if not dry_run:
            atomic_write_text(sp, json.dumps(state, indent=1) + "\n")

    def brief(r: dict) -> dict:
        d = {"line": r["line"], "command": r["command"], "status": r["status"]}
        if "seconds" in r:
            d["seconds"] = r["seconds"]
        if r["status"] == "FAILED":
            d |= {"exit": r["exit"], "tail": r["tail"][-300:]}
        return d

    count = lambda pred: sum(1 for r in results if pred(r["status"]))  # noqa: E731
    return {"file": str(batch_file), "state": str(sp), "ok": count(lambda s: s == "ok"),
            "failed": count(lambda s: s == "FAILED"), "skipped": count(lambda s: s.startswith("skipped")),
            "not_run": count(lambda s: s in ("not run", "would run")), "lines": [brief(r) for r in results]}
