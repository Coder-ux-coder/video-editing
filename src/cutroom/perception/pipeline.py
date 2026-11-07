"""Run analysis steps on demand, each only once (idempotent, cached in analysis/<source>/)."""

from __future__ import annotations

import json

import numpy as np

from cutroom.job import Job, SourceRecord, atomic_write_text
from cutroom.paths import rel_or_abs
from cutroom.perception.audio import clipping, levels, measure_loudness, read_mono, silences
from cutroom.perception.words import WordsFile, build_words, load_words

WORDS_VERSION = 5   # bump when refinement changes so words.json is rebuilt


def ensure_audio(job: Job, s: SourceRecord) -> None:
    from cutroom.ingest.proxy import extract_audio

    if not s.audio.get("analysis") or not job.abspath(s.audio["analysis"]).is_file():
        extract_audio(job, s)


def ensure_transcript(job: Job, s: SourceRecord, *, on_progress=None):
    from cutroom.perception.transcribe import load_transcript, save_transcript, transcribe_audio
    from cutroom.system.profile import load_profile

    if s.transcript and (job.root / s.transcript).is_file():
        return load_transcript(job.root / s.transcript)
    ensure_audio(job, s)
    asr = load_profile().asr
    t = transcribe_audio(job.abspath(s.audio["asr"]), asr, time_offset=s.media.primary_audio.start_time,
                         source_id=s.id, on_progress=on_progress)
    paths = save_transcript(t, job.analysis_dir(s.id))
    s.transcript = rel_or_abs(paths["json"], job.root)
    job.save()
    job.log.note(f"{s.id}: transcribed with {asr.model} ({len(t.words)} words, RTF {t.rtf}).")
    return t


def ensure_words(job: Job, s: SourceRecord, *, force: bool = False, on_progress=None) -> WordsFile:
    """Edit-ready words (refined timings, fillers, untranscribed sounds) for one source."""
    from cutroom.style import load_style

    path = job.analysis_dir(s.id) / "words.json"
    if path.is_file() and not force:
        wf = load_words(path)
        if wf.stats.get("version") == WORDS_VERSION:
            return wf
    t = ensure_transcript(job, s, on_progress=on_progress)
    ensure_audio(job, s)
    fillers = set(load_style(client=job.manifest.client)["editing"]["fillers_always"])
    wf, env = build_words(t, job.abspath(s.audio["analysis"]), job.abspath(s.audio["asr"]),
                          source_id=s.id, fillers=fillers)
    wf.stats["version"] = WORDS_VERSION
    atomic_write_text(path, wf.model_dump_json(indent=1, by_alias=True) + "\n")
    np.savez_compressed(job.analysis_dir(s.id) / "envelope.npz", env=env)
    st = wf.stats
    job.log.note(f"{s.id}: words refined ({st['edges_refined']} pause edges moved, mean {st['mean_abs_shift_ms']} ms, "
                 f"max {st['max_abs_shift_ms']} ms); {st['transcribed_fillers']} transcribed fillers, "
                 f"{st['untranscribed_voiced']} untranscribed voiced sounds, "
                 f"{st['untranscribed_unvoiced']} breaths/noises in pauses.")
    return wf


def audio_report(job: Job, s: SourceRecord, *, force: bool = False) -> dict:
    """Loudness, true peak, LRA, noise floor, clipping, silences: analysis/<id>/audio_report.json."""
    path = job.analysis_dir(s.id) / "audio_report.json"
    if path.is_file() and not force:
        return json.loads(path.read_text(encoding="utf-8"))
    ensure_audio(job, s)
    flac = job.abspath(s.audio["analysis"])
    x, sr = read_mono(flac)
    env_path = job.analysis_dir(s.id) / "envelope.npz"
    if env_path.is_file():
        env = np.load(env_path)["env"]
    else:
        from cutroom.perception.audio import envelope_db

        env = envelope_db(x, sr)
    floor, speech, thr = levels(env)
    loud = measure_loudness(flac)
    sil = silences(env, thr, 0.3)
    total = len(x) / sr
    report = {
        "source": s.id, "duration_s": round(total, 3),
        "integrated_lufs": loud.integrated_lufs, "loudness_range_lu": loud.lra_lu,
        "true_peak_dbtp": loud.true_peak_dbtp,
        "noise_floor_dbfs": floor, "speech_level_dbfs": speech, "speech_threshold_dbfs": thr,
        "snr_db": round(speech - floor, 1),
        "clipping": clipping(x, sr),
        "silences_over_0_3s": len(sil), "silence_seconds": round(sum(b - a for a, b in sil), 2),
        "longest_silences": sorted(({"start": a, "end": b, "dur": round(b - a, 2)} for a, b in sil),
                                   key=lambda d: -d["dur"])[:10],
        "flags": [],
    }
    if loud.true_peak_dbtp is not None and loud.true_peak_dbtp > -1.0:
        report["flags"].append(f"true peak {loud.true_peak_dbtp} dBTP above -1 dBTP (normalise/limit in the audio stage)")
    if report["clipping"]["clipped_runs"]:
        report["flags"].append(f"{report['clipping']['clipped_runs']} clipped runs; first at {report['clipping']['first_at']}s")
    if report["snr_db"] < 25:
        report["flags"].append(f"low speech-to-noise ratio ({report['snr_db']} dB): denoise in the audio stage")
    if loud.integrated_lufs is not None and loud.integrated_lufs < -30:
        report["flags"].append(f"very quiet recording ({loud.integrated_lufs} LUFS)")
    atomic_write_text(path, json.dumps(report, indent=1) + "\n")
    return report


def ensure_proxy(job: Job, s: SourceRecord):
    from cutroom.ingest.proxy import make_proxy

    if s.media.kind == "video" and (not s.proxy or not job.abspath(s.proxy).is_file()):
        make_proxy(job, s)
    return job.abspath(s.proxy) if s.proxy else None


def visual_report(job: Job, s: SourceRecord, *, force: bool = False, speech_coverage: float | None = None) -> dict:
    """Shots, faces, framing, exposure/blur per shot, contact sheets: analysis/<id>/visual_report.json."""
    from cutroom.perception import visual as V

    path = job.analysis_dir(s.id) / "visual_report.json"
    if path.is_file() and not force:
        return json.loads(path.read_text(encoding="utf-8"))
    if s.media.kind != "video":
        return {}
    proxy = ensure_proxy(job, s)
    dur = s.media.duration
    shots = V.detect_shots(proxy, dur)
    samples = V.sample_video(proxy, dur, every=1.0)
    shot_reports = [V.summarize_shot(sh, samples) for sh in shots]
    sheets_dir = job.analysis_dir(s.id) / "sheets"
    n = 16
    times = [dur * (k + 0.5) / n for k in range(n)]
    V.contact_sheet(V.grab_frames(proxy, times), [f"{s.id} {_tc(t)}" for t in times], sheets_dir / "overview.jpg",
                    cols=4, title=f"{s.id} overview: {n} frames evenly spaced over {_tc(dur)}")
    for k, sh in enumerate(shot_reports[:40], 1):
        ts = [sh["start"] + sh["duration"] * (i + 0.5) / 6 for i in range(6)]
        p = V.contact_sheet(V.grab_frames(proxy, ts), [f"shot {k} {_tc(t)}" for t in ts],
                            sheets_dir / f"shot_{k:03d}.jpg", cols=3,
                            title=f"{s.id} shot {k}: {_tc(sh['start'])}-{_tc(sh['end'])}")
        sh["sheet"] = rel_or_abs(p, job.root)
    faces = [{"t": f.t, "faces": f.faces[:3]} for f in samples]
    atomic_write_text(job.analysis_dir(s.id) / "faces.json", json.dumps(faces) + "\n")
    cov = speech_coverage if speech_coverage is not None else 0.0
    report = {"source": s.id, "duration_s": dur, "shots": shot_reports, "shot_count": len(shot_reports),
              "samples": len(samples), "overview_sheet": rel_or_abs(sheets_dir / "overview.jpg", job.root),
              "footage_type": V.classify_footage(cov, shots, samples, dur),
              "flags": sorted({f for sh in shot_reports for f in sh.get("flags", [])})}
    atomic_write_text(path, json.dumps(report, indent=1) + "\n")
    return report


def pacing(job: Job, s: SourceRecord, *, force: bool = False) -> dict:
    from cutroom.perception.pacing import pacing_map

    path = job.analysis_dir(s.id) / "pacing.json"
    if path.is_file() and not force:
        return json.loads(path.read_text(encoding="utf-8"))
    wf = ensure_words(job, s)
    env = np.load(job.analysis_dir(s.id) / "envelope.npz")["env"]
    rep = pacing_map(wf, env, s.media.duration)
    atomic_write_text(path, json.dumps(rep) + "\n")
    return rep


def _tc(t: float) -> str:
    m, sec = divmod(int(t), 60)
    return f"{m:02d}:{sec:02d}"
