"""Publishing text for a Short, written by the agent and checked here.

The agent writes ``publish.json`` from ``publish.task.md``; the check refuses invented numbers
and hype the Short does not deliver, and enforces YouTube's limits (title 100 characters,
description 5,000)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from cutroom.graphics.facts import numbers_in, supported
from cutroom.job import Job, atomic_write_text
from cutroom.timeline import store

TITLE_MAX = 100          # YouTube title limit
DESC_MAX = 5000          # YouTube description limit
HYPE = {"shocking", "insane", "unbelievable", "you won't believe", "gone wrong", "mind-blowing", "secret they",
        "exposed", "destroyed"}


def short_text(job: Job) -> str:
    from cutroom.captions.plan import timeline_words
    from cutroom.timeline.ops import words_for

    tl = store.load(job)
    words = {s.id: words_for(job, s.id) for s in tl.sources}
    return " ".join(w.text for w in timeline_words(tl, words, set()))


def write_task(job: Job) -> Path:
    tl = store.load(job)
    text = short_text(job)
    brief = (job.root / "brief.md").read_text(encoding="utf-8") if (job.root / "brief.md").is_file() else ""
    task = f"""# Task: publishing text for {job.id}

Write `publish.json` in this job folder, then run `cutroom short-publish {job.id} --check`.

```json
{{"title": "…", "description": "…", "hashtags": ["#Shorts", "#…", "#…"]}}
```

Rules:
- Title ≤ {TITLE_MAX} characters, says what the viewer gets; the strongest idea first. No hype
  the Short does not deliver, no all-caps words.
- Description: one or two sentences about this Short, then the source credit if the brief has one,
  then a pointer to the full video if there is one.
- 3–5 hashtags, first `#Shorts`, the others specific to the content (no generic #viral).
- Every number or claim must be said in the Short (the check verifies numbers).

Duration: {tl.duration:.1f} s. Transcript of the Short:

> {text}

Brief:

{brief}
"""
    p = job.root / "publish.task.md"
    atomic_write_text(p, task)
    return p


def check_publish(job: Job) -> dict:
    p = job.root / "publish.json"
    if not p.is_file():
        return {"ok": False, "problems": ["publish.json is missing"]}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"ok": False, "problems": [f"publish.json is not valid JSON: {e}"]}
    problems = []
    title, desc, tags = d.get("title", ""), d.get("description", ""), d.get("hashtags", [])
    if not title or len(title) > TITLE_MAX:
        problems.append(f"title must be 1-{TITLE_MAX} characters (is {len(title)})")
    if len(desc) > DESC_MAX:
        problems.append(f"description is {len(desc)} characters (max {DESC_MAX})")
    if not 3 <= len(tags) <= 5 or not tags or tags[0].lower() != "#shorts":
        problems.append("3-5 hashtags, the first one #Shorts")
    if any(not re.fullmatch(r"#\w+", t) for t in tags):
        problems.append("hashtags must be # followed by letters/digits only")
    if re.search(r"\b[A-Z]{4,}\b", title):
        problems.append("no all-caps words in the title")
    said = short_text(job)
    credit = _credit_lines(job)
    pool = numbers_in(said) + [n for line in credit for n in numbers_in(line)]
    for field, text in (("title", title), ("description", desc)):
        for n in numbers_in(text):
            if not supported(n, pool):
                problems.append(f"{field} shows {n:g}, which the Short does not say")
    low = (title + " " + desc).lower()
    hype = [h for h in HYPE if h in low]
    if hype:
        problems.append(f"hype words: {', '.join(hype)}")
    if credit and not any(c.lower() in desc.lower() for c in credit):
        problems.append(f"description must carry the credit from the brief: {credit[0]}")
    return {"ok": not problems, "problems": problems, "title": title, "hashtags": tags}


def _credit_lines(job: Job) -> list[str]:
    """Lines after a 'Credit:' label in the brief (source footage credits)."""
    brief = job.root / "brief.md"
    if not brief.is_file():
        return []
    return [m.group(1).strip() for m in re.finditer(r"(?im)^\s*credit:\s*(.+)$", brief.read_text(encoding="utf-8"))]
