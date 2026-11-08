"""Hand the edit to a person in an NLE: OTIO (DaVinci Resolve), FCPXML 1.10 (Resolve, Final Cut
Pro), CMX3600 EDL (Premiere, Avid, anything).

Every cut is exported on the same frame grid the renderer uses, so the NLE plays exactly what
Cutroom rendered. Each file is read back and compared with the timeline (clip count, every source
range, total frames) before it is reported as written. Captions travel as .srt next to them;
graphics travel as markers with their parameters (the NLE cannot run Remotion).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from cutroom.errors import CutroomError
from cutroom.ingest.probe import fps_rational
from cutroom.job import Job, atomic_write_text
from cutroom.render.engine import plan_segments
from cutroom.timeline.model import Timeline


def _fps_frac(fps: float) -> Fraction:
    num, _, den = fps_rational(fps).partition("/")
    return Fraction(int(num), int(den or 1))


def _src_path(job: Job, tl: Timeline, sid: str) -> Path:
    return job.source_path(job.source(sid))


def _source_frames(job: Job, tl: Timeline, sid: str) -> int:
    return int(round(tl.source(sid).duration * tl.meta.fps))


# --------------------------------------------------------------------------- OTIO


def to_otio(job: Job, tl: Timeline):
    import opentimelineio as otio

    fps = float(_fps_frac(tl.meta.fps))
    rt = lambda f: otio.opentime.RationalTime(f, fps)                       # noqa: E731
    tr = lambda a, n: otio.opentime.TimeRange(rt(a), rt(n))                  # noqa: E731
    out = otio.schema.Timeline(name=f"{job.id} v{tl.meta.version:03d}", global_start_time=rt(0))
    out.metadata["cutroom"] = {"job": job.id, "version": tl.meta.version, "fps": tl.meta.fps,
                               "width": tl.meta.width, "height": tl.meta.height}
    refs = {s.id: otio.schema.ExternalReference(target_url=_src_path(job, tl, s.id).resolve().as_uri(),
                                                available_range=tr(0, _source_frames(job, tl, s.id)))
            for s in tl.sources}
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1 dialogue", kind=otio.schema.TrackKind.Audio)
    segs = plan_segments(tl)
    for s in segs:
        for track in (v1, a1):
            track.append(otio.schema.Clip(name=s.clip_id, media_reference=refs[s.source].clone(),
                                          source_range=tr(s.n_in, s.frames), metadata={"cutroom": {"clip": s.clip_id}}))
    total = sum(s.frames for s in segs)
    out.tracks.append(v1)
    if tl.broll:
        from cutroom.assets.library import asset_path, get_asset

        v2 = otio.schema.Track(name="V2 b-roll", kind=otio.schema.TrackKind.Video)
        pos = 0
        for b in sorted(tl.broll, key=lambda b: b.start):
            f0, f1 = int(round(b.start * fps)), int(round(b.end * fps))
            if f0 > pos:
                v2.append(otio.schema.Gap(source_range=tr(0, f0 - pos)))
            ref = otio.schema.ExternalReference(target_url=asset_path(get_asset(b.asset)).resolve().as_uri())
            v2.append(otio.schema.Clip(name=b.id, media_reference=ref,
                                       source_range=tr(int(round(b.src_in * fps)), f1 - f0),
                                       metadata={"cutroom": {"asset": b.asset, "license": b.license, "credit": b.credit,
                                                             "motion": b.motion if b.kind == "image" else None}}))
            pos = f1
        out.tracks.append(v2)
    out.tracks.append(a1)
    for name, beds in (("A2 music", tl.audio.music), ("A3 effects", tl.audio.sfx)):
        if not beds:
            continue
        from cutroom.assets.library import asset_path, get_asset

        tr_ = otio.schema.Track(name=name, kind=otio.schema.TrackKind.Audio)
        pos = 0
        for b in sorted(beds, key=lambda b: b.start):
            f0, f1 = int(round(b.start * fps)), int(round(b.end * fps))
            if f0 > pos:
                tr_.append(otio.schema.Gap(source_range=tr(0, f0 - pos)))
            ref = otio.schema.ExternalReference(target_url=asset_path(get_asset(b.asset)).resolve().as_uri())
            tr_.append(otio.schema.Clip(name=b.id, media_reference=ref, source_range=tr(int(round(b.src_in * fps)), f1 - f0),
                                        metadata={"cutroom": {"gain_db": b.gain_db, "duck": b.duck, "license": b.license}}))
            pos = f1
        out.tracks.append(tr_)
    for m in tl.markers:
        v1.markers.append(otio.schema.Marker(name=m.text, marked_range=tr(int(round(m.t * fps)), 1),
                                             color=otio.schema.MarkerColor.YELLOW, metadata={"kind": m.kind}))
    for g in tl.graphics:
        v1.markers.append(otio.schema.Marker(name=f"{g.template}: {_gtext(g)}",
                                             marked_range=tr(int(round(g.start * fps)), max(1, int(round((g.end - g.start) * fps)))),
                                             color=otio.schema.MarkerColor.PURPLE,
                                             metadata={"cutroom_graphic": {"template": g.template, "variant": g.variant,
                                                                           "params": g.params, "facts": g.facts}}))
    return out, total


def _gtext(g) -> str:
    for k in ("name", "title", "text", "label", "handle", "to"):
        if k in g.params:
            return str(g.params[k])
    return g.template


def write_otio(job: Job, tl: Timeline, path: Path) -> dict:
    import opentimelineio as otio

    ot, total = to_otio(job, tl)
    otio.adapters.write_to_file(ot, str(path))
    back = otio.adapters.read_from_file(str(path))
    v1 = back.tracks[0]
    segs = plan_segments(tl)
    got = [(c.name, int(round(c.source_range.start_time.value)), int(round(c.source_range.duration.value))) for c in v1]
    want = [(s.clip_id, s.n_in, s.frames) for s in segs]
    if got != want or int(round(back.duration().value)) != total:
        raise CutroomError("The OTIO file does not read back as the timeline.", fix="Report this as a bug.",
                           details=f"{got[:3]} vs {want[:3]}")
    return {"path": str(path), "clips": len(got), "frames": total, "tracks": [t.name for t in back.tracks],
            "markers": len(v1.markers), "verified": True}


# --------------------------------------------------------------------------- FCPXML 1.10


def _t(frames: int, fd: Fraction) -> str:
    """Frames → FCPXML rational seconds on the frame grid ("1001/30000s" units)."""
    v = Fraction(frames) * fd
    return f"{v.numerator}/{v.denominator}s" if v.denominator != 1 else f"{v.numerator}s"


def write_fcpxml(job: Job, tl: Timeline, path: Path) -> dict:
    fps = _fps_frac(tl.meta.fps)
    fd = 1 / fps                                         # frame duration (s)
    segs = plan_segments(tl)
    total = sum(s.frames for s in segs)
    root = ET.Element("fcpxml", version="1.10")
    res = ET.SubElement(root, "resources")
    ET.SubElement(res, "format", id="r1", frameDuration=_t(1, fd), width=str(tl.meta.width),
                  height=str(tl.meta.height), name=f"Cutroom {tl.meta.width}x{tl.meta.height} {float(fps):.3f}")
    ids: dict[str, str] = {}
    for k, s in enumerate(tl.sources, 2):
        aid = f"r{k}"
        ids[s.id] = aid
        a = ET.SubElement(res, "asset", id=aid, name=s.id, start="0s", duration=_t(_source_frames(job, tl, s.id), fd),
                          hasVideo="1" if s.has_video else "0", hasAudio="1" if s.has_audio else "0", format="r1",
                          audioSources="1", audioChannels="2", audioRate="48000")
        ET.SubElement(a, "media-rep", kind="original-media", src=_src_path(job, tl, s.id).resolve().as_uri())
    extra = {}
    nxt = len(tl.sources) + 2
    from cutroom.assets.library import asset_path, get_asset

    for b in [*tl.broll, *tl.audio.music, *tl.audio.sfx]:
        if b.asset in extra:
            continue
        a_ = get_asset(b.asset)
        aid = f"r{nxt}"
        nxt += 1
        extra[b.asset] = aid
        is_img = a_.kind == "image"
        el = ET.SubElement(res, "asset", id=aid, name=a_.title or a_.id, start="0s",
                           duration="0s" if is_img else _t(int(round((a_.duration or 600) * float(fps))), fd),
                           hasVideo="1" if a_.kind in ("image", "video") else "0",
                           hasAudio="1" if a_.kind in ("music", "sfx", "video") else "0")
        if is_img:
            el.set("format", "r1")
        ET.SubElement(el, "media-rep", kind="original-media", src=asset_path(a_).resolve().as_uri())
    lib = ET.SubElement(root, "library")
    ev = ET.SubElement(lib, "event", name=f"Cutroom {job.id}")
    proj = ET.SubElement(ev, "project", name=f"{job.id} v{tl.meta.version:03d}")
    seq = ET.SubElement(proj, "sequence", format="r1", duration=_t(total, fd), tcStart="0s", tcFormat="NDF",
                        audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(seq, "spine")
    clips_xml = []
    for s in segs:
        el = ET.SubElement(spine, "asset-clip", ref=ids[s.source], name=s.clip_id, offset=_t(s.out_frame, fd),
                           start=_t(s.n_in, fd), duration=_t(s.frames, fd), format="r1", tcFormat="NDF")
        clips_xml.append((s, el))

    def parent_at(frame: int):
        for s, el in clips_xml:
            if s.out_frame <= frame < s.out_frame + s.frames:
                return s, el
        return clips_xml[-1]

    fpsf = float(fps)
    for lane, items in ((1, tl.broll), (-1, tl.audio.music), (-2, tl.audio.sfx)):
        for b in items:
            f0, f1 = int(round(b.start * fpsf)), int(round(b.end * fpsf))
            s, el = parent_at(f0)
            local = s.n_in + (f0 - s.out_frame)            # connected clips are timed in the parent's source time
            tag = "video" if getattr(b, "kind", "") == "image" else "asset-clip"
            ET.SubElement(el, tag, ref=extra[b.asset], lane=str(lane), name=b.id, offset=_t(local, fd),
                          start=_t(int(round(b.src_in * fpsf)), fd), duration=_t(f1 - f0, fd))
    for m in tl.markers:
        f = int(round(m.t * fpsf))
        s, el = parent_at(f)
        ET.SubElement(el, "chapter-marker" if m.kind == "chapter" else "marker", start=_t(s.n_in + f - s.out_frame, fd),
                      duration=_t(1, fd), value=m.text, **({"posterOffset": "0s"} if m.kind == "chapter" else {}))
    for g in tl.graphics:
        f = int(round(g.start * fpsf))
        s, el = parent_at(f)
        ET.SubElement(el, "marker", start=_t(s.n_in + f - s.out_frame, fd), duration=_t(max(1, int(round((g.end - g.start) * fpsf))), fd),
                      value=f"Graphic {g.template}: {_gtext(g)}"[:120])
    ET.indent(root)
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="unicode") + "\n"
    atomic_write_text(path, xml)
    # read back
    back = ET.parse(path).getroot()
    spine_b = back.find("./library/event/project/sequence/spine")
    got = [(c.get("name"), Fraction(c.get("start").rstrip("s")) / fd, Fraction(c.get("duration").rstrip("s")) / fd)
           for c in spine_b.findall("asset-clip")]
    want = [(s.clip_id, s.n_in, s.frames) for s in segs]
    seq_d = Fraction(back.find("./library/event/project/sequence").get("duration").rstrip("s")) / fd
    if [(n, int(a), int(b)) for n, a, b in got] != want or seq_d != total:
        raise CutroomError("The FCPXML does not read back as the timeline.", fix="Report this as a bug.")
    return {"path": str(path), "clips": len(got), "frames": total, "connected": len(tl.broll) + len(tl.audio.music) + len(tl.audio.sfx),
            "markers": len(tl.markers) + len(tl.graphics), "verified": True}


# --------------------------------------------------------------------------- CMX3600 EDL


def _tc(frames: int, base: int) -> str:
    s, f = divmod(frames, base)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def write_edl(job: Job, tl: Timeline, path: Path) -> dict:
    """Main picture and dialogue (one event per clip). Non-drop-frame timecode counted at the
    nominal rate (30 for 29.97), as NLEs expect for NDF EDLs."""
    base = int(round(tl.meta.fps))
    segs = plan_segments(tl)
    lines = [f"TITLE: {job.id} v{tl.meta.version:03d}"[:70], "FCM: NON-DROP FRAME", ""]
    for k, s in enumerate(segs, 1):
        reel = s.source[:8]
        lines.append(f"{k:03d}  {reel:<8} B     C        {_tc(s.n_in, base)} {_tc(s.n_out, base)} "
                     f"{_tc(s.out_frame, base)} {_tc(s.out_frame + s.frames, base)}")
        lines.append(f"* FROM CLIP NAME: {_src_path(job, tl, s.source).name}")
        lines.append(f"* CUTROOM CLIP: {s.clip_id}")
        lines.append("")
    atomic_write_text(path, "\n".join(lines))
    # read back
    events = [ln.split() for ln in path.read_text(encoding="utf-8").splitlines() if ln[:3].isdigit()]

    def fr(tc: str) -> int:
        h, m, s_, f = (int(x) for x in tc.split(":"))
        return ((h * 60 + m) * 60 + s_) * base + f
    rec = 0
    for ev, s in zip(events, segs):
        si, so, ri, ro = (fr(x) for x in ev[4:8])
        if (si, so - si, ri, ro - ri) != (s.n_in, s.frames, rec, s.frames):
            raise CutroomError("The EDL does not read back as the timeline.", fix="Report this as a bug.")
        rec = ro
    if len(events) != len(segs):
        raise CutroomError("The EDL event count is wrong.", fix="Report this as a bug.")
    return {"path": str(path), "events": len(events), "frames": rec, "verified": True,
            "note": "picture + dialogue only (CMX3600 has one video track); b-roll, music and graphics are in the OTIO/FCPXML"}


def export_all(job: Job, tl: Timeline, out_dir: Path, formats: tuple[str, ...] = ("otio", "fcpxml", "edl")) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{job.id}_v{tl.meta.version:03d}"
    res = {}
    if "otio" in formats:
        res["otio"] = write_otio(job, tl, out_dir / f"{stem}.otio")
    if "fcpxml" in formats:
        res["fcpxml"] = write_fcpxml(job, tl, out_dir / f"{stem}.fcpxml")
    if "edl" in formats:
        res["edl"] = write_edl(job, tl, out_dir / f"{stem}.edl")
    if tl.captions and tl.captions.cues:
        from cutroom.captions.export import to_srt

        atomic_write_text(out_dir / f"{stem}.srt", to_srt(tl.captions))
        res["srt"] = str(out_dir / f"{stem}.srt")
    return res
