"""Publishing text for the long-form video: title, description with chapters and credits, tags.

The agent writes ``publish.json`` from ``publish.task.md`` (which proposes chapters from the topic
map); the check enforces YouTube's rules and Cutroom's: numbers only if said, chapters valid
(first at 0:00, at least 3, each at least 10 s, ascending), every licence credit present.
Applying it writes the chapters into the timeline as markers and renders description.txt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from cutroom.graphics.facts import numbers_in, supported
from cutroom.job import Job, atomic_write_text
from cutroom.timeline import store
from cutroom.timeline.model import Marker, Timeline
from cutroom.timeline.outline import tc

TITLE_MAX, DESC_MAX, TAGS_MAX_CHARS, HASHTAGS_MAX = 100, 5000, 500, 15
CHAPTER_MIN_S, CHAPTERS_MIN = 10.0, 3


def propose_chapters(job: Job, tl: Timeline) -> list[dict]:
    """Runs of the edit that stay on one topic (topics.json), in timeline order; runs under 10 s
    join the previous chapter; the first chapter starts at 0:00."""
    from cutroom.captions.plan import timeline_words
    from cutroom.timeline.ops import words_for
    from cutroom.timeline.planning import _sentences

    tp = job.root / "analysis" / "topics.json"
    topics = json.loads(tp.read_text(encoding="utf-8")).get("topics", []) if tp.is_file() else []
    if not topics:
        return []
    words = {s.id: words_for(job, s.id) for s in tl.sources}
    sent_of: dict[tuple[str, int], int] = {}
    for sid, wf in words.items():
        if wf:
            for k, sent in enumerate(_sentences(wf.words)):
                for w in sent:
                    sent_of[(sid, round(w.start, 3))] = k
    topic_of = {}
    for t in topics:
        for k in range(t["first_sentence"], t["last_sentence"] + 1):
            topic_of[(t.get("source", "S01"), k)] = t
    runs: list[dict] = []
    for c in tl.track_clips():
        wf = words.get(c.source)
        if not wf:
            continue
        for w in wf.words:
            if not (c.src_in <= (w.start + w.end) / 2 < c.src_out):
                continue
            t = topic_of.get((c.source, sent_of.get((c.source, round(w.start, 3)))))
            if t is None:
                continue
            at = c.to_timeline(max(w.start, c.src_in))
            if not runs or runs[-1]["topic"] != t["id"]:
                runs.append({"topic": t["id"], "title": t["title"], "t": at, "end": at})
            runs[-1]["end"] = c.to_timeline(min(w.end, c.src_out))
    chapters: list[dict] = []
    for r in runs:
        if chapters and (r["end"] - r["t"] < CHAPTER_MIN_S or r["t"] - chapters[-1]["t"] < CHAPTER_MIN_S):
            continue                               # too short to be its own chapter
        if chapters and chapters[-1]["topic"] == r["topic"]:
            continue
        chapters.append({"t": round(r["t"], 1), "title": r["title"], "topic": r["topic"]})
    if chapters:
        chapters[0]["t"] = 0.0
    return chapters


def credits(job: Job, tl: Timeline) -> list[str]:
    out = []
    brief = job.root / "brief.md"
    if brief.is_file():
        out += [m.group(1).strip() for m in re.finditer(r"(?im)^\s*credit:\s*(.+)$", brief.read_text(encoding="utf-8"))]
    seen = set()
    for b in [*tl.broll, *tl.audio.music, *tl.audio.sfx]:
        if b.credit and b.credit not in seen:
            seen.add(b.credit)
            out.append(b.credit)
    return out


def write_task(job: Job) -> Path:
    tl = store.load(job)
    chapters = propose_chapters(job, tl)
    cr = credits(job, tl)
    brief = (job.root / "brief.md").read_text(encoding="utf-8") if (job.root / "brief.md").is_file() else ""
    ch = "\n".join(f"{tc(c['t'], ms=False)} {c['title']}" for c in chapters) or "(no topic map: write chapters by hand)"
    task = f"""# Task: publishing text for {job.id} (v{tl.meta.version:03d}, {tc(tl.duration, ms=False)})

Write `publish.json`, then run `cutroom publish {job.id} --check` (it also writes the chapters into
the timeline and `delivery/description.txt`).

```json
{{"title": "…", "description": "…", "chapters": [{{"t": 0, "title": "…"}}],
  "hashtags": ["#…"], "tags": ["…"]}}
```

Rules: title ≤ {TITLE_MAX} characters, honest, strongest idea first, no all-caps words, no hype the
video does not deliver. Description ≤ {DESC_MAX} characters: what the viewer gets, in the speaker's
terms; the chapters and credits are appended automatically (do not repeat them). Chapters: first
at 0:00, at least {CHAPTERS_MIN}, each at least {CHAPTER_MIN_S:.0f} s, ascending, short titles. Up to
{HASHTAGS_MAX} hashtags (3 is best); tags ≤ {TAGS_MAX_CHARS} characters in total. Numbers must be said in the video.

Proposed chapters (from analysis/topics.json; rename, merge or move them):

{ch}

Credits that will be appended:

{chr(10).join('- ' + c for c in cr) or '- (none)'}

Brief:

{brief}
"""
    p = job.root / "publish.task.md"
    atomic_write_text(p, task)
    return p


def _said(job: Job, tl: Timeline) -> str:
    from cutroom.captions.plan import timeline_words
    from cutroom.timeline.ops import words_for

    words = {s.id: words_for(job, s.id) for s in tl.sources}
    return " ".join(w.text for w in timeline_words(tl, words, set()))


def check(job: Job) -> dict:
    p = job.root / "publish.json"
    if not p.is_file():
        return {"ok": False, "problems": ["publish.json is missing"]}
    d = json.loads(p.read_text(encoding="utf-8"))
    tl = store.load(job)
    problems = []
    title, desc = d.get("title", ""), d.get("description", "")
    if not title or len(title) > TITLE_MAX:
        problems.append(f"title must be 1-{TITLE_MAX} characters")
    if re.search(r"\b[A-Z]{5,}\b", title):
        problems.append("no all-caps words in the title")
    chapters = sorted(d.get("chapters", []), key=lambda c: c["t"])
    if chapters:
        if chapters[0]["t"] != 0:
            problems.append("the first chapter must start at 0:00")
        if len(chapters) < CHAPTERS_MIN:
            problems.append(f"at least {CHAPTERS_MIN} chapters (YouTube ignores fewer)")
        ends = [c["t"] for c in chapters[1:]] + [tl.duration]
        short = [c["title"] for c, e in zip(chapters, ends) if e - c["t"] < CHAPTER_MIN_S]
        if short:
            problems.append(f"chapters shorter than {CHAPTER_MIN_S:.0f} s: {', '.join(short)}")
        if any(c["t"] >= tl.duration for c in chapters):
            problems.append("a chapter starts after the end of the video")
    tags = d.get("tags", [])
    if len(", ".join(tags)) > TAGS_MAX_CHARS:
        problems.append(f"tags exceed {TAGS_MAX_CHARS} characters")
    hashtags = d.get("hashtags", [])
    if len(hashtags) > HASHTAGS_MAX or any(not re.fullmatch(r"#\w+", h) for h in hashtags):
        problems.append(f"hashtags: # followed by letters/digits, at most {HASHTAGS_MAX}")
    said = _said(job, tl)
    cr = credits(job, tl)
    pool = numbers_in(said) + [n for c in cr for n in numbers_in(c)]
    for field, text in (("title", title), ("description", desc),
                        *(("chapter", c["title"]) for c in chapters)):
        for n in numbers_in(text):
            if not supported(n, pool):
                problems.append(f"{field} shows {n:g}, which the video does not say")
    full = description_text(d, chapters, cr)
    if len(full) > DESC_MAX:
        problems.append(f"description with chapters and credits is {len(full)} characters (max {DESC_MAX})")
    return {"ok": not problems, "problems": problems, "title": title, "chapters": chapters, "description": full}


def description_text(d: dict, chapters: list[dict], cr: list[str]) -> str:
    parts = [d.get("description", "").strip()]
    if chapters:
        parts.append("\n".join(f"{_yt(c['t'])} {c['title']}" for c in chapters))
    if cr:
        parts.append("Credits:\n" + "\n".join(cr))
    if d.get("hashtags"):
        parts.append(" ".join(d["hashtags"]))
    return "\n\n".join(p for p in parts if p) + "\n"


def _yt(t: float) -> str:
    t = int(t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def apply(job: Job, res: dict) -> int | None:
    """Chapters into the timeline (markers, a new version) and delivery/description.txt."""
    tl = store.load(job).model_copy(deep=True)
    (job.root / "delivery").mkdir(exist_ok=True)
    atomic_write_text(job.root / "delivery" / "description.txt",
                      f"{res['title']}\n\n{res['description']}")
    want = [(round(c["t"], 1), c["title"]) for c in res["chapters"]]
    have = [(round(m.t, 1), m.text) for m in tl.markers if m.kind == "chapter"]
    if want == have:
        return None
    tl.markers = [m for m in tl.markers if m.kind != "chapter"] + [
        Marker(id=f"ch{k:02d}", t=c["t"], kind="chapter", text=c["title"]) for k, c in enumerate(res["chapters"], 1)]
    return store.save_new(job, tl, command="publish", summary=f"{len(res['chapters'])} chapters from publish.json")
