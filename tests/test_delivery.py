"""Phase 7: NLE export round trips, publishing checks and chapters, revision rounds, the delivery
gate, and the job-state guide."""

import json
import xml.etree.ElementTree as ET

import pytest

from cutroom.errors import RefusedError


@pytest.fixture
def cut_job(jobs_base, tmp_path):
    """A small real job: luma-coded source, words, two cuts, captions."""
    from test_cutting import seq
    from test_render import _luma_coded_source

    from cutroom.captions.plan import plan_captions
    from cutroom.ingest.ingest import ingest
    from cutroom.ingest.proxy import extract_audio, make_proxy
    from cutroom.job import Job
    from cutroom.style import load_style
    from cutroom.timeline import store
    from cutroom.timeline.ops import Removal, assemble, remove_ranges

    src = tmp_path / "coded.mp4"
    _luma_coded_source(src)
    job = Job.create("deliver-test", client="acme")
    ingest(job, [src])
    s = job.manifest.sources[0]
    make_proxy(job, s)
    extract_audio(job, s)
    wf = seq("This is the first sentence here. And the second one follows now. A third short one.", start=0.3,
             word=0.25)
    (job.root / "analysis" / "S01").mkdir(parents=True, exist_ok=True)
    (job.root / "analysis" / "S01" / "words.json").write_text(wf.model_dump_json(by_alias=True))
    tl = assemble(job)
    remove_ranges(tl, [Removal("S01", 2.0, 3.0, "manual", "t"), Removal("S01", 5.0, 7.0, "manual", "t")],
                  tool="t", version=2, min_clip=0.1)
    tl.captions = plan_captions(tl, {"S01": wf}, load_style(), "clean")
    store.save_new(job, tl, command="t", summary="t")
    return job


def test_nle_exports_read_back_as_the_timeline(cut_job, tmp_path):
    import opentimelineio as otio

    from cutroom.export.nle import export_all
    from cutroom.render.engine import plan_segments
    from cutroom.timeline import store

    tl = store.load(cut_job)
    res = export_all(cut_job, tl, tmp_path / "nle")
    segs = plan_segments(tl)
    assert all(res[k]["verified"] for k in ("otio", "fcpxml", "edl"))
    ot = otio.adapters.read_from_file(res["otio"]["path"])
    assert [int(c.source_range.duration.value) for c in ot.tracks[0]] == [s.frames for s in segs]
    assert [t.name for t in ot.tracks] == ["V1", "A1 dialogue"]
    root = ET.parse(res["fcpxml"]["path"]).getroot()
    assert root.get("version") == "1.10" and len(root.findall(".//spine/asset-clip")) == len(segs)
    assert root.find(".//format").get("frameDuration") == "1/25s"
    edl = open(res["edl"]["path"]).read()
    assert "FCM: NON-DROP FRAME" in edl and edl.count("\n001  ") == 1 and "00:00:02:00" in edl
    assert (tmp_path / "nle" / "deliver-test_v001.srt").is_file()


def test_publish_checks_chapters_numbers_and_credits(cut_job):
    from cutroom import publish as P

    (cut_job.root / "brief.md").write_text("Credit: Footage: Test Archive (public domain)\n")
    good = {"title": "A good honest title", "description": "What you get.",
            "chapters": [], "hashtags": ["#Test"], "tags": ["a"]}           # a 7 s video: no chapters
    (cut_job.root / "publish.json").write_text(json.dumps(good))
    res = P.check(cut_job)
    assert res["ok"] and "Footage: Test Archive" in res["description"]
    bad = {**good, "title": "AMAZING 99 facts", "chapters": [{"t": 1, "title": "a"}, {"t": 2, "title": "b"}]}
    (cut_job.root / "publish.json").write_text(json.dumps(bad))
    probs = " | ".join(P.check(cut_job)["problems"])
    assert "0:00" in probs and "at least 3" in probs and "shorter than 10 s" in probs
    assert "all-caps" in probs and "99" in probs
    n = P.apply(cut_job, P.check(cut_job) | {"chapters": [{"t": 0, "title": "Start"}], "title": "t", "description": "d"})
    from cutroom.timeline import store

    assert n and [m.text for m in store.load(cut_job).markers] == ["Start"]
    assert (cut_job.root / "delivery" / "description.txt").is_file()


def test_revision_notes_map_through_later_cuts_and_apply_traceably(cut_job):
    from cutroom import revise as R
    from cutroom.timeline import store
    from cutroom.timeline.ops import Removal, remove_ranges

    notes = R.parse_notes("Notes on v1:\n- 0:04 please cut this bit\n- make the music quieter\nnot a bullet\n")
    assert [n["n"] for n in notes] == [1, 2] and notes[0]["times_seen"] == [4.0]
    seen = store.load(cut_job, 1)
    now = seen.model_copy(deep=True)
    remove_ranges(now, [Removal("S01", 0.5, 1.5, "manual", "t")], tool="t", version=3, min_clip=0.1)
    store.save_new(cut_job, now, command="t", summary="later cut")
    m = R.map_time(seen, store.load(cut_job), 4.0)
    assert m["now"] == pytest.approx(3.0, abs=0.05)                          # 1 s cut before it since
    rd = R.prepare(cut_job, "- 0:04 please cut this bit\n- looks great", 1)
    plan = json.loads((rd / "plan.json").read_text())
    with pytest.raises(RefusedError, match="without a plan"):
        R.apply(cut_job, rd.name)
    plan["notes"][0]["commands"] = ["cutroom snapshot <job> -m 'client note 1'"]
    plan["notes"][1]["response"] = "Thanks, kept."
    (rd / "plan.json").write_text(json.dumps(plan))
    calls = []
    res = R.apply(cut_job, rd.name, runner=lambda argv: (calls.append(argv), (True, "ok"))[1])
    assert calls == [["snapshot", "deliver-test", "-m", "client note 1"]]
    assert res["notes"][0]["ok"] and "Thanks, kept." in (rd / "report.md").read_text()
    plan["notes"][0]["commands"] = ["cutroom approve <job> --package x --by me"]
    (rd / "plan.json").write_text(json.dumps(plan))
    with pytest.raises(RefusedError, match="cannot be part of a revision plan"):
        R.apply(cut_job, rd.name, runner=lambda argv: (True, ""))


def test_delivery_refuses_until_ready_and_approval_needs_a_name(cut_job):
    from cutroom import deliver as D

    with pytest.raises(RefusedError) as e:
        D.prepare(cut_job)
    msg = str(e.value)
    assert "no final render" in msg and "not been reviewed" in msg and "thumbnail" in msg and "publish.json" in msg
    with pytest.raises(RefusedError):
        D.approve(cut_job, "nothing_here", "Me")


def test_next_lists_stages_in_order(cut_job):
    from cutroom.tools.delivery_tools import job_state

    st = {s["stage"]: s for s in job_state(cut_job)}
    assert st["ingest"]["done"] and st["captions"]["done"]
    assert not st["final render"]["done"] and "--mode final" in st["final render"]["next"]
    assert st["approval (a person)"]["note"] == "the human gate"


def test_client_style_scaffold_loads(tmp_path, monkeypatch):
    import cutroom.style as S

    monkeypatch.setattr(S, "styles_dir", lambda: tmp_path)
    (tmp_path / "default.style.yaml").write_text(open(S.__file__.replace("src/cutroom/style.py", "styles/default.style.yaml")).read())
    from cutroom.tools.timeline_tools import client_style

    client_style("acme")
    st = S.load_style("default", client="acme")
    assert st["graphics"]["theme"]["colors"]["primary"] == "#6D28D9" and st["captions"]["default_style"] == "clean"
    with pytest.raises(RefusedError):
        client_style("acme")
