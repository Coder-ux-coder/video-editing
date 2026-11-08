"""Speaker detection: who speaks when, per sentence."""

from __future__ import annotations

from typing import Annotated, Optional

import typer

from cutroom.cli_common import JobArg, JsonOpt, SourcesOpt, emit, job_command, out
from cutroom.errors import RefusedError
from cutroom.job import Job
from cutroom.tools.registry import ToolManifest, tool

TESTS = ["tests/test_speakers.py"]


@tool(ToolManifest("speakers", "Who speaks when: speaker per sentence (local pyannote community-1 model).",
                   "analysis", inputs=["analysis audio", "words.json"], outputs=["analysis/<source>/speakers.json"],
                   tests=TESTS))
def speakers(job_id: JobArg, sources: SourcesOpt = None,
             num: Annotated[Optional[int], typer.Option("--num-speakers", help="Exact number of speakers, if known.")] = None,
             min_: Annotated[Optional[int], typer.Option("--min-speakers")] = None,
             max_: Annotated[Optional[int], typer.Option("--max-speakers")] = None,
             name: Annotated[Optional[list[str]], typer.Option("--name", help="Name a speaker: SPEAKER_00=Interviewer (repeat).")] = None,
             force: Annotated[bool, typer.Option(help="Run again even if speakers.json exists.")] = False,
             as_json: JsonOpt = False) -> None:
    """Runs on this machine (the model is downloaded once; pyannote's telemetry is switched off).
    Each sentence gets the speaker who talks most of its time; the voice with the most speech is
    the main speaker. Lists every other speaker's sentences, so an interviewer's questions can be
    named (--name) and cut (`cutroom cut <job> --speaker SPEAKER_00 --reason ...`)."""
    from cutroom.perception import speakers as SP

    job = Job.load(job_id)
    srcs = [s for s in job.manifest.sources if not sources or s.id in sources]
    names = {}
    for item in name or []:
        if "=" not in item:
            raise RefusedError(f"Bad --name '{item}'.", fix="Use --name SPEAKER_00=Interviewer.")
        k, v = item.split("=", 1)
        names[k.strip()] = v.strip()
    res = {}
    with job_command(job, "speakers") as summary:
        for s in srcs:
            if names and not force and SP.load(job, s.id):
                doc = SP.set_names(job, s.id, names)
            else:
                doc = SP.diarize(job, s, num_speakers=num, min_speakers=min_, max_speakers=max_, force=force)
                if names:
                    doc = SP.set_names(job, s.id, names)
            res[s.id] = {k: doc[k] for k in ("main", "names", "speakers", "elapsed_s")}
            res[s.id]["others"] = [{"n": x["n"], "speaker": x["speaker"], "start": x["start"], "text": x["text"],
                                   "confidence": x["confidence"]} for x in doc["sentences"] if x["speaker"] != doc["main"]]
        summary.update({sid: {"speakers": len(r["speakers"])} for sid, r in res.items()})
    emit(as_json, res)
    if not as_json:
        for sid, r in res.items():
            doc = SP.load(job, sid)
            out.print(f"[bold]{sid}[/]: {len(r['speakers'])} speaker(s)")
            for sp in r["speakers"]:
                tag = " (main)" if sp["id"] == r["main"] else ""
                out.print(f"  {SP.label(doc, sp['id'])}{tag}: {sp['sentences']} sentence(s), {sp['seconds']}s "
                          f"({sp['share']:.0%})")
            for x in r["others"][:40]:
                out.print(f"  [{x['n']}] {x['start']:7.2f}s {SP.label(doc, x['speaker'])}: {x['text'][:90]}")
