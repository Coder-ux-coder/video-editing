"""Golden end-to-end regression: the bundled synthetic clip (a stumble, a retake, pauses) from raw
footage to a delivery package awaiting approval, through the real CLI, one process per command,
exactly as the agent runs it. The transcript is frozen (tests/golden/), so no speech model is needed.

What the edit must be is pinned in tests/golden/doctor_pipeline.json: the cuts, the clips, the
captions, the graphic, every render self-check, the review verdict and the package contents.
A change in any of them fails here with a readable diff. After an intended change:

    CUTROOM_UPDATE_GOLDEN=1 pytest tests/test_golden.py -m slow
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT, needs_ffmpeg
from test_graphics import _remotion_ready


GOLDEN = ROOT / "tests" / "golden" / "doctor_pipeline.json"
TOPICS = {"schema": "cutroom.topics/1", "topics": [
    {"id": "t01", "source": "S01", "title": "Welcome", "summary": "Greeting; what the clip is about.",
     "first_sentence": 0, "last_sentence": 1, "usefulness": 3, "hook_candidate": False, "short_candidate": False,
     "notes": ""},
    {"id": "t02", "source": "S01", "title": "The recipe", "summary": "Flour and water, a rest, 220 degrees.",
     "first_sentence": 2, "last_sentence": 5, "usefulness": 5, "hook_candidate": True, "short_candidate": False,
     "notes": "Sentence 4 repeats 3: a retake."},
    {"id": "t03", "source": "S01", "title": "Goodbye", "summary": "Sign-off.", "first_sentence": 6,
     "last_sentence": 6, "usefulness": 2, "hook_candidate": False, "short_candidate": False, "notes": ""}]}
PUBLISH = {"title": "A simple loaf of bread at home", "description": "Flour and water, a two-hour rest, then 220 "
           "degrees for 40 minutes.", "chapters": [], "hashtags": ["#Baking"], "tags": ["bread", "baking"]}


def cutroom(*args: str, ok: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run([sys.executable, "-m", "cutroom", *args], capture_output=True, text=True, env=os.environ.copy())
    if ok and p.returncode != 0:
        raise AssertionError(f"cutroom {' '.join(args)} failed:\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")
    return p


def fingerprint(job) -> dict:
    """What a person would notice if it changed, in frames (robust to float noise)."""
    from cutroom.timeline import store

    tl = store.load(job)
    fps = tl.meta.fps
    f = lambda s: int(round(s * fps))  # noqa: E731
    final = json.loads((job.root / "renders" / f"v{tl.meta.version:03d}_final.json").read_text())
    review = json.loads((job.root / "review" / f"v{tl.meta.version:03d}" / "review.json").read_text())
    pkg = job.root / "delivery" / f"{job.id}_v{tl.meta.version:03d}"
    manifest = json.loads((pkg / "MANIFEST.json").read_text())
    return {
        "steps": [c.command for c in tl.meta.changelog],
        "frames": f(tl.duration),
        "clips": [[c.source, f(c.src_in), f(c.src_out)] for c in tl.track_clips()],
        "cuts": sorted([c.kind, f(c.src_in), f(c.src_out)] for c in tl.active_cuts()),
        "captions": [c.text for c in tl.captions.cues],
        "graphics": [[g.template, f(g.start), f(g.end), g.params.get("to")] for g in tl.graphics],
        "music_beds": len(tl.audio.music),
        "voice_chain": bool(tl.audio.voice_chain),
        "final": {"size": final["size"], "frames": f(final["duration_s"]), "checks": final["checks"]},
        "review": {"counts": review["scorecard"]["counts"], "ready": review["scorecard"]["ready"],
                   "issues": sorted(i["check"] for i in review["issues"])},
        "package": {"status": manifest["status"],
                    "files": sorted(p.relative_to(pkg).as_posix() for p in pkg.rglob("*") if p.is_file())},
    }


def diff(want, got, path="") -> list[str]:
    """Readable differences; numbers (frames) may differ by one (decoder rounding across FFmpeg builds)."""
    if isinstance(want, dict) and isinstance(got, dict):
        out = []
        for k in sorted(set(want) | set(got)):
            if k not in got or k not in want:
                out.append(f"{path}/{k}: {'missing' if k not in got else 'new'}")
            else:
                out += diff(want[k], got[k], f"{path}/{k}")
        return out
    if isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            return [f"{path}: {len(want)} items expected, got {len(got)}:\n    want {want}\n    got  {got}"]
        return [d for a, b, i in zip(want, got, range(len(want))) for d in diff(a, b, f"{path}[{i}]")]
    if isinstance(want, (int, float)) and isinstance(got, (int, float)) and not isinstance(want, bool):
        return [] if abs(want - got) <= 1 else [f"{path}: expected {want}, got {got}"]
    return [] if want == got else [f"{path}: expected {want!r}, got {got!r}"]


@needs_ffmpeg
@pytest.mark.slow
@pytest.mark.skipif(not _remotion_ready(), reason="graphics package not installed")
def test_raw_clip_to_delivery_package_matches_golden(jobs_base, monkeypatch):
    from cutroom.job import Job

    monkeypatch.setenv("CUTROOM_JOBS_DIR", str(jobs_base))
    sample = ROOT / "assets" / "samples" / "doctor_sample.mp4"
    cutroom("new", "golden", "--title", "Golden clip")
    cutroom("ingest", "golden", str(sample))
    job = Job.load("golden")
    s = job.manifest.sources[0]
    (job.root / "analysis" / s.id).mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "tests" / "golden" / "doctor_sample.transcript.json", job.root / "analysis" / s.id / "transcript.json")
    s.transcript = f"analysis/{s.id}/transcript.json"
    job.save()
    with open(job.root / "brief.md", "a", encoding="utf-8") as fh:
        fh.write("\nCredit: Footage: Cutroom test clip (CC0)\n")

    cutroom("analyze", "golden")                                  # words refined from the frozen transcript
    (job.root / "analysis" / "topics.json").write_text(json.dumps(TOPICS))
    cutroom("topics", "golden", "--check")
    cutroom("rough-cut", "golden")
    cutroom("audio", "golden")
    cutroom("captions", "golden")
    # The fact rule end to end: a number nobody said is refused, the one said is accepted.
    bad = cutroom("graphic-add", "golden", "number-counter", "--sentence", "5", "-p", "to=250", "-p", "suffix= degrees",
                  "-f", "sentences:5", ok=False)
    assert bad.returncode != 0 and "250" in bad.stdout + bad.stderr
    cutroom("graphic-add", "golden", "number-counter", "--sentence", "5", "-p", "to=220", "-p", "suffix= degrees",
            "-p", "label=Oven", "-f", "sentences:5", "--reason", "the one number to remember")
    cutroom("music", "golden", "--generate", "calm", "--whole")
    cutroom("review", "golden", "--fix")
    cutroom("publish", "golden")
    (job.root / "publish.json").write_text(json.dumps({**PUBLISH, "title": "A loaf of bread in four steps"}))
    assert cutroom("publish", "golden", "--check", ok=False).returncode != 0      # "four" is never said
    (job.root / "publish.json").write_text(json.dumps(PUBLISH))
    cutroom("publish", "golden", "--check")
    cutroom("thumbnails", "golden", "--text", "Bake a simple loaf", "--at", "4")  # no face in this clip
    cutroom("thumbnails", "golden", "--text", "Bake a simple loaf", "--pick", "1")
    assert cutroom("deliver", "golden", ok=False).returncode != 0                # no final render yet
    cutroom("render", "golden", "--mode", "final")
    cutroom("review", "golden")
    cutroom("export", "golden")
    cutroom("deliver", "golden")
    state = json.loads(cutroom("next", "golden", "--json").stdout)
    pending = [s["stage"] for s in state["stages"] if not s["done"]]
    assert pending == ["shorts", "approval (a person)"]            # no Shorts asked for this clip
    rep = json.loads(cutroom("report", "golden", "--json").stdout)
    assert rep["cost"]["usd"] == 0.0 and rep["time"]["commands"]["rough-cut"]["runs"] == 1

    got = fingerprint(Job.load("golden"))
    if os.environ.get("CUTROOM_UPDATE_GOLDEN") or not GOLDEN.is_file():
        GOLDEN.write_text("{\n" + ",\n".join(f" {json.dumps(k)}: {json.dumps(v)}" for k, v in got.items()) + "\n}\n")
        if not os.environ.get("CUTROOM_UPDATE_GOLDEN"):
            pytest.fail(f"Wrote a new golden file {GOLDEN.relative_to(ROOT)}; check it and commit it.")
        return
    problems = diff(json.loads(GOLDEN.read_text()), got)
    assert not problems, "The edit changed:\n  " + "\n  ".join(problems)


def test_diff_tolerates_one_frame_and_reports_the_rest():
    assert diff({"a": [1, 2], "b": "x"}, {"a": [2, 2], "b": "x"}) == []
    d = diff({"a": [1, 2], "b": "x", "c": True}, {"a": [4, 2], "b": "y", "c": False})
    assert d == ["/a[0]: expected 1, got 4", "/b: expected 'x', got 'y'", "/c: expected True, got False"]
