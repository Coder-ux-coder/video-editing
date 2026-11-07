"""Topic map as an agent-in-the-loop task.

Topic segmentation needs judgement, and the only intelligence in Cutroom is the agent
itself (no paid API). So ``prepare`` writes a task file with numbered sentences and the
exact output schema; the agent writes ``analysis/topics.json``; ``check`` validates it.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from cutroom.perception.words import WordsFile


class Topic(BaseModel):
    id: str = Field(description="t01, t02, ...")
    source: str
    title: str = Field(description="3-8 words")
    summary: str = Field(description="One or two sentences.")
    first_sentence: int
    last_sentence: int
    usefulness: int = Field(ge=1, le=5, description="1 = cut candidate, 5 = must keep")
    hook_candidate: bool = False
    short_candidate: bool = False
    notes: str = ""


class TopicMap(BaseModel):
    schema_: str = Field("cutroom.topics/1", alias="schema")
    topics: list[Topic]

    model_config = {"populate_by_name": True}


def sentences(wf: WordsFile) -> list[dict]:
    out, cur = [], []
    for w in wf.words:
        cur.append(w)
        if w.sentence_end:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"n": k, "start": s[0].start, "end": s[-1].end, "text": " ".join(w.text for w in s)}
            for k, s in enumerate(out)]


def prepare(words: dict[str, WordsFile], out: Path) -> Path:
    lines = ["# Task: topic map", "",
             "Split the transcript below into topics (typically 30 s to 3 min each), in order, covering every",
             "sentence exactly once per source. For each topic give a short title, a one-to-two sentence",
             "summary, a usefulness rating (1 = filler/off-topic, cut candidate; 5 = essential), whether it",
             "contains a strong hook line (a compelling opening for the video) and whether it could stand alone",
             "as a YouTube Short (self-contained idea, strong first line, 20-60 s). Base every judgement only",
             "on what is said; do not invent facts.", "",
             "Write the answer to `analysis/topics.json` with this shape, then run `cutroom topics <job> --check`:",
             "", "```json", json.dumps({"schema": "cutroom.topics/1", "topics": [
                 {"id": "t01", "source": "S01", "title": "Who Dan is", "summary": "...", "first_sentence": 0,
                  "last_sentence": 4, "usefulness": 3, "hook_candidate": False, "short_candidate": False,
                  "notes": ""}]}, indent=1), "```", ""]
    for sid, wf in words.items():
        lines += [f"## Source {sid}", ""]
        for s in sentences(wf):
            lines.append(f"[{s['n']}] {s['start']:7.2f}-{s['end']:7.2f} {s['text']}")
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def check(path: Path, words: dict[str, WordsFile]) -> list[str]:
    if not path.is_file():
        return [f"{path} does not exist yet (write it from analysis/topics.task.md)"]
    try:
        tm = TopicMap.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as e:
        return [f"schema: {err['loc']}: {err['msg']}" for err in e.errors()]
    problems = []
    for sid, wf in words.items():
        n = len(sentences(wf))
        ts = [t for t in tm.topics if t.source == sid]
        if not ts:
            problems.append(f"{sid}: no topics")
            continue
        expect = 0
        for t in ts:
            if t.first_sentence != expect:
                problems.append(f"{t.id}: starts at sentence {t.first_sentence}, expected {expect} (gap or overlap)")
            if t.last_sentence < t.first_sentence:
                problems.append(f"{t.id}: last_sentence before first_sentence")
            expect = t.last_sentence + 1
        if expect != n:
            problems.append(f"{sid}: topics end at sentence {expect - 1}, transcript has {n} (0-{n - 1})")
    return problems
