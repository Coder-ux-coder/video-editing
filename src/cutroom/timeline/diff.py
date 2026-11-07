"""Human-readable difference between two timeline versions."""

from __future__ import annotations

from cutroom.timeline.model import Timeline
from cutroom.timeline.outline import tc


def diff(a: Timeline, b: Timeline) -> dict:
    ca = {c.id: c for c in a.track_clips()}
    cb = {c.id: c for c in b.track_clips()}
    cuts_a = {x.id: x for x in a.cuts}
    cuts_b = {x.id: x for x in b.cuts}
    new_cuts = [x for i, x in cuts_b.items() if i not in cuts_a and not x.restored]
    restored = [x for i, x in cuts_b.items() if x.restored and (i not in cuts_a or not cuts_a[i].restored)]
    changed = []
    for i in ca.keys() & cb.keys():
        x, y = ca[i], cb[i]
        fields = [f for f in ("src_in", "src_out", "speed", "volume_db", "locked", "reframe", "effects", "video", "audio")
                  if getattr(x, f) != getattr(y, f)]
        if fields:
            changed.append({"clip": i, "fields": fields})
    meta = [f for f in ("fps", "width", "height", "style", "footage_type") if getattr(a.meta, f) != getattr(b.meta, f)]
    return {
        "from": a.meta.version, "to": b.meta.version,
        "duration": [round(a.duration, 3), round(b.duration, 3)],
        "clips_added": sorted(cb.keys() - ca.keys()), "clips_removed": sorted(ca.keys() - cb.keys()),
        "clips_changed": sorted(changed, key=lambda d: d["clip"]),
        "cuts_added": [x.model_dump() for x in sorted(new_cuts, key=lambda x: (x.source, x.src_in))],
        "cuts_restored": [x.id for x in restored],
        "graphics": [len(a.graphics), len(b.graphics)], "markers": [len(a.markers), len(b.markers)],
        "captions": [len(a.captions.cues) if a.captions else 0, len(b.captions.cues) if b.captions else 0],
        "meta_changed": meta,
        "changelog": [e.summary for e in b.meta.changelog if e.version > a.meta.version],
    }


def diff_text(a: Timeline, b: Timeline, max_cuts: int = 40) -> str:
    d = diff(a, b)
    da, db = d["duration"]
    lines = [f"v{a.meta.version:03d} → v{b.meta.version:03d}: duration {tc(da)} → {tc(db)} ({db - da:+.2f}s)"]
    for s in d["changelog"]:
        lines.append(f"  · {s}")
    if d["clips_added"] or d["clips_removed"]:
        lines.append(f"  clips: +{len(d['clips_added'])} −{len(d['clips_removed'])} (now {len(b.track_clips())})")
    for c in d["clips_changed"][:20]:
        lines.append(f"  changed {c['clip']}: {', '.join(c['fields'])}")
    if d["cuts_added"]:
        total = sum(x["src_out"] - x["src_in"] for x in d["cuts_added"])
        lines.append(f"  {len(d['cuts_added'])} new cuts removing {total:.2f}s:")
        for x in d["cuts_added"][:max_cuts]:
            said = f' "{x["text"]}"' if x["text"] else ""
            lines.append(f"    ✂ {x['id']} {x['source']} {tc(x['src_in'])} {x['src_out'] - x['src_in']:.2f}s "
                         f"{x['kind']}{said}: {x['reason']}")
        if len(d["cuts_added"]) > max_cuts:
            lines.append(f"    … and {len(d['cuts_added']) - max_cuts} more (`cutroom show --format cuts`)")
    if d["cuts_restored"]:
        lines.append(f"  restored cuts: {', '.join(d['cuts_restored'])}")
    for key in ("graphics", "markers", "captions"):
        x, y = d[key]
        if x != y:
            lines.append(f"  {key}: {x} → {y}")
    if d["meta_changed"]:
        lines.append(f"  settings changed: {', '.join(d['meta_changed'])}")
    return "\n".join(lines) + "\n"
