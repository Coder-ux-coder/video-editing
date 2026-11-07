"""Phase 4 acceptance: plant known defects in the HEAD timeline of a job, as one new version.

    python scripts/acceptance/phase4_seed.py accept-review

Seeds (each one a check must find):
  1. two shortened pauses restored            → audio.long_pauses
  2. one removed filler restored               → audio.fillers
  3. voice chain removed                       → audio.no_chain
  4. captions left as they were (now stale)    → captions.stale
  5. a list graphic forced over the face       → graphics.face   (measured on the render)
  6. a counter with a number nobody said       → graphics.unsourced
  7. a lower third shown for 0.6 s             → graphics.too_short
  8. punch-ins removed                         → cuts.jump_cuts
Writes seeds.json next to the job's review folder with what was planted.
"""

from __future__ import annotations

import json
import sys

from cutroom.job import Job
from cutroom.timeline import store
from cutroom.timeline.model import Graphic
from cutroom.timeline.ops import restore_cut


def main(job_id: str) -> None:
    job = Job.load(job_id)
    tl = store.load(job).model_copy(deep=True)
    seeds = []
    silences = sorted((c for c in tl.active_cuts() if c.kind == "silence" and 0.6 < c.src_out - c.src_in < 3.0
                       and c.src_in > 5.0), key=lambda c: -(c.src_out - c.src_in))[:2]
    for c in silences:
        restore_cut(tl, c.id)
        seeds.append({"seed": "long pause restored", "cut": c.id, "s": round(c.src_out - c.src_in, 2), "check": "audio.long_pauses"})
    filler = next((c for c in tl.active_cuts() if c.kind == "filler" and c.text.lower().strip(",.") in ("um", "uh")), None)
    if filler:
        restore_cut(tl, filler.id)
        seeds.append({"seed": "filler restored", "cut": filler.id, "text": filler.text, "check": "audio.fillers"})
    tl.audio.voice_chain = {}
    seeds.append({"seed": "voice chain removed", "check": "audio.no_chain"})
    seeds.append({"seed": "captions not re-planned after the restores", "check": "captions.stale"})
    face = [{"x": 0.0, "y": 0.0}]
    fp = job.root / "analysis" / tl.sources[0].id / "faces.json"
    if fp.is_file():
        fs = [s["faces"][0] for s in json.loads(fp.read_text(encoding="utf-8")) if s["faces"]]
        face = fs[len(fs) // 2:len(fs) // 2 + 1] or face
    side = "center-right" if face[0]["x"] + face[0].get("w", 0) / 2 > 0.5 else "center-left"
    x = 0.94 if side == "center-right" else 0.06
    w, h = 0.42, 0.45
    box = [x - w, 0.36 - h / 2, x, 0.36 + h / 2] if side == "center-right" else [x, 0.36 - h / 2, x + w, 0.36 + h / 2]
    t_mid = round(tl.duration * 0.6, 2)
    clip = next(c for c in tl.track_clips() if c.start <= t_mid < c.end)
    tl.graphics += [
        Graphic(id="g0101", template="list-reveal", variant="numbered", start=t_mid, end=t_mid + 5.0,
                params={"title": "Tracks found here", "items": ["Human", "Sloth", "Mammoth"]},
                placement={"anchor": side, "x": x, "y": 0.36, "box": box}, facts=[],
                attach={"source": clip.source, "t": round(clip.to_source(t_mid), 3), "offset": 0.0},
                reason="seeded: placed over the face"),
        Graphic(id="g0102", template="number-counter", variant="inline", start=8.0, end=11.0,
                params={"to": 30000, "suffix": " tracks"}, placement={"anchor": "center-left", "x": 0.06, "y": 0.4,
                                                                    "box": [0.06, 0.29, 0.36, 0.51]},
                fact_check={"ok": False, "unsupported_numbers": [{"param": "to", "value": 30000}]},
                reason="seeded: a number nobody said"),
        Graphic(id="g0103", template="lower-third", variant="pill", start=2.0, end=2.6,
                params={"name": "Dan Odess", "title": "Chief Scientist"},
                placement={"anchor": "bottom-left", "x": 0.06, "y": 0.75, "box": [0.06, 0.63, 0.40, 0.75]},
                reason="seeded: too short to read"),
    ]
    seeds += [{"seed": "list over the face", "graphic": "g0101", "check": "graphics.face"},
              {"seed": "unsourced number", "graphic": "g0102", "check": "graphics.unsourced"},
              {"seed": "0.6 s lower third", "graphic": "g0103", "check": "graphics.too_short"}]
    for c in tl.clips:
        c.reframe = []
    seeds.append({"seed": "punch-ins removed", "check": "cuts.jump_cuts"})
    n = store.save_new(job, tl, command="seed-defects", summary=f"acceptance: {len(seeds)} defects planted")
    (job.root / "review").mkdir(exist_ok=True)
    (job.root / "review" / "seeds.json").write_text(json.dumps({"version": n, "seeds": seeds}, indent=1) + "\n",
                                                     encoding="utf-8")
    print(f"v{n:03d}: planted {len(seeds)} defects")
    for s in seeds:
        print("  ", s)


if __name__ == "__main__":
    main(sys.argv[1])
