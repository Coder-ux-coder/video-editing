"""Human/agent-readable views of a timeline: the outline, the cut list and a one-line summary."""

from __future__ import annotations

import textwrap
from collections import Counter

from cutroom.perception.words import WordsFile
from cutroom.timeline.model import Timeline


def tc(t: float, ms: bool = True) -> str:
    """Timecode HH:MM:SS.mmm (or MM:SS.mmm when under an hour)."""
    neg = t < 0
    total_ms = int(round(abs(t) * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    core = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    return ("-" if neg else "") + core + (f".{msec:03d}" if ms else "")


QUIET_DB = 10.0   # speech this far below the speaker's median level is probably off-mic


def _median_level(wf: WordsFile | None) -> float | None:
    if wf is None:
        return None
    lv = sorted(w.level_db for w in wf.words if w.level_db is not None)
    return lv[len(lv) // 2] if lv else None


def _words_text(wf: WordsFile | None, a: float, b: float, median: float | None = None) -> str:
    """Words in [a, b). Doubtful words are marked ⟨?word?⟩; quiet stretches (likely off-mic
    voices: an interviewer, someone in the room) are marked ⟪quiet: …⟫."""
    if wf is None:
        return ""
    parts, quiet = [], []

    def flush() -> None:
        if quiet:
            parts.append("⟪quiet: " + " ".join(quiet) + "⟫")
            quiet.clear()

    for w in wf.words:
        if not a <= (w.start + w.end) / 2 < b:
            continue
        text = f"⟨?{w.text}?⟩" if w.prob < 0.5 else w.text
        if median is not None and w.level_db is not None and w.level_db < median - QUIET_DB:
            quiet.append(text)
            continue
        flush()
        parts.append(text)
    flush()
    return " ".join(parts)


def summary(tl: Timeline) -> dict:
    cuts = tl.active_cuts()
    by_kind = Counter()
    secs = Counter()
    for c in cuts:
        by_kind[c.kind] += 1
        secs[c.kind] += c.duration
    src_total = sum(s.duration for s in tl.sources)
    return {"version": tl.meta.version, "duration_s": round(tl.duration, 3), "clips": len(tl.track_clips()),
            "sources_duration_s": round(src_total, 3), "cuts": len(cuts),
            "removed_s": round(sum(c.duration for c in cuts), 3),
            "cuts_by_kind": {k: {"count": by_kind[k], "seconds": round(secs[k], 2)} for k in sorted(by_kind)},
            "graphics": len(tl.graphics), "markers": len(tl.markers)}


def outline(tl: Timeline, words: dict[str, WordsFile | None], *, width: int = 110, show_cuts: bool = True) -> str:
    """The edit as text: each clip with its timeline/source times and spoken words,
    and between clips the cuts that were made there, with their reasons."""
    s = summary(tl)
    lines = [f"# Timeline v{tl.meta.version:03d} | {tc(tl.duration)} | {s['clips']} clips | "
             f"{s['cuts']} cuts removed {s['removed_s']:.1f}s of {s['sources_duration_s']:.1f}s source",
             "# " + ", ".join(f"{k}: {v['count']} ({v['seconds']:.1f}s)" for k, v in s["cuts_by_kind"].items()),
             "# ⟨?word?⟩ = low-confidence transcription; ⟪quiet: …⟫ = speech ≥10 dB below the speaker (off-mic?)",
             ""]
    clips = tl.track_clips()
    medians = {sid: _median_level(wf) for sid, wf in words.items()}
    cuts = sorted(tl.active_cuts(), key=lambda c: (c.source, c.src_in))
    markers = sorted(tl.markers, key=lambda m: m.t)
    for k, c in enumerate(clips):
        for m in [m for m in markers if c.start - 1e-6 <= m.t < c.end]:
            lines.append(f"  ◆ {m.kind.upper()} @ {tc(m.t)}: {m.text}")
        text = _words_text(words.get(c.source), c.src_in, c.src_out, medians.get(c.source)) or "(no speech)"
        head = f"[{tc(c.start)} → {tc(c.end)}] {c.id} {c.source} {tc(c.src_in)}–{tc(c.src_out)}"
        if c.locked:
            head += " [locked]"
        lines.append(head)
        lines += textwrap.wrap(text, width=width, initial_indent="    ", subsequent_indent="    ")
        for g in tl.graphics:
            if c.start - 1e-6 <= g.start < c.end:
                lines.append(f"    ▣ {g.template} {tc(g.start)}–{tc(g.end)} {g.params.get('text', '')}".rstrip())
        if show_cuts and k + 1 < len(clips):
            nxt = clips[k + 1]
            lo, hi = (c.src_out, nxt.src_in) if nxt.source == c.source else (c.src_out, float("inf"))
            between = [x for x in cuts if x.source == c.source and x.src_in >= lo - 1e-3 and x.src_out <= hi + 1e-3]
            for x in between:
                conf = f" {x.confidence:.2f}" if x.confidence is not None else ""
                said = f' "{x.text}"' if x.text else ""
                lines.append(f"    ✂ {x.id} {x.kind} {x.duration:.2f}s{said}: {x.reason}{conf}")
    return "\n".join(lines) + "\n"


def cut_list(tl: Timeline) -> str:
    lines = [f"# Cuts in v{tl.meta.version:03d} (source time)"]
    for x in sorted(tl.cuts, key=lambda c: (c.source, c.src_in)):
        state = " [restored]" if x.restored else ""
        conf = f" conf {x.confidence:.2f}" if x.confidence is not None else ""
        said = f' "{x.text}"' if x.text else ""
        lines.append(f"{x.id} {x.source} {tc(x.src_in)}–{tc(x.src_out)} ({x.duration:.2f}s) {x.kind}{said}: "
                     f"{x.reason}{conf} [{x.tool}, v{x.version:03d}]{state}")
    return "\n".join(lines) + "\n"
