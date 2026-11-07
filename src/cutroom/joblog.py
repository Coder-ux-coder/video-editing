"""Human-readable decision log (``log.md``) plus a machine-readable command log.

The point of ``log.md`` is that a person can read it top to bottom and
understand every decision the agent made, with the reason and confidence.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class JobLog:
    def __init__(self, job_root: Path):
        self.root = Path(job_root)
        self.md_path = self.root / "log.md"
        self.jsonl_path = self.root / "logs" / "commands.jsonl"

    def _append(self, text: str) -> None:
        self.md_path.parent.mkdir(parents=True, exist_ok=True)
        with self.md_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def init(self, job_id: str) -> None:
        if not self.md_path.exists():
            self._append(f"# Job log: {job_id}\n\nEvery command and decision, newest at the bottom.\n")

    def section(self, title: str) -> None:
        self._append(f"\n## {_stamp()} · {title}\n\n")

    def note(self, text: str) -> None:
        self._append(f"- {text}\n")

    def warn(self, text: str) -> None:
        self._append(f"- **Warning:** {text}\n")

    def decision(self, what: str, reason: str, confidence: float | None = None) -> None:
        conf = f", confidence {confidence:.2f}" if confidence is not None else ""
        self._append(f"- **Decision:** {what}: {reason}{conf}\n")

    def command(self, name: str, argv: list[str], status: str, elapsed_s: float,
                summary: dict | None = None) -> None:
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"at": now_iso(), "command": name, "argv": argv, "status": status,
                 "elapsed_s": round(elapsed_s, 2), "summary": summary or {}}
        with self.jsonl_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry) + "\n")
