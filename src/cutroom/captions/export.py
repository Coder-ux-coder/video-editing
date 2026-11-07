"""Caption files: SubRip (.srt) and WebVTT (.vtt), from the planned cues (timeline time)."""

from __future__ import annotations

from cutroom.timeline.model import Captions


def _ts(t: float, sep: str) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _lines(cue) -> list[str]:
    return [" ".join(cue.words[i]["text"] for i in line) for line in cue.lines] or [cue.text]


def to_srt(caps: Captions) -> str:
    blocks = []
    for k, c in enumerate(caps.cues, 1):
        blocks.append(f"{k}\n{_ts(c.start, ',')} --> {_ts(c.end, ',')}\n" + "\n".join(_lines(c)) + "\n")
    return "\n".join(blocks)


def to_vtt(caps: Captions) -> str:
    out = ["WEBVTT", ""]
    for c in caps.cues:
        out.append(f"{_ts(c.start, '.')} --> {_ts(c.end, '.')}")
        out += _lines(c)
        out.append("")
    return "\n".join(out)
