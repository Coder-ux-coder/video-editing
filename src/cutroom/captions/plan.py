"""Plan captions: phrases, line breaks, timing, emphasis, placement and contrast scrims.

Captions are planned from the refined words (≤ 80 ms timing), mapped through the edit into
timeline time. Rules come from the style file (`captions.*`), see EDITING_STANDARDS.md §6.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from cutroom.perception.textnorm import normalize
from cutroom.perception.words import WordsFile
from cutroom.timeline.model import CaptionCue, Captions, Timeline

# Never end a line on these (a dangling article/preposition/conjunction reads badly).
WEAK_END = {"a", "an", "the", "of", "to", "in", "on", "at", "for", "with", "by", "from", "and", "or", "but",
            "my", "your", "his", "her", "its", "our", "their", "this", "that", "these", "those", "is", "are",
            "was", "were", "be", "as", "if", "so", "than", "into", "about", "very", "not", "i", "we", "you",
            "during", "through", "over", "under", "after", "before", "between", "without", "within", "across",
            "around", "behind", "toward", "towards", "upon", "since", "until", "while", "because", "when",
            "where", "who", "which", "whose", "whether", "can", "could", "will", "would", "should", "must",
            "have", "has", "had", "been", "being", "just", "really", "more", "most", "less", "such", "both",
            "they", "he", "she", "it", "there"}
STOPWORDS = WEAK_END | {"it", "he", "she", "they", "them", "there", "here", "what", "which", "who", "have",
                        "has", "had", "do", "does", "did", "can", "could", "would", "will", "just", "like",
                        "know", "mean", "really", "kind", "sort", "then", "also", "some", "all"}
DETERMINERS = {"a", "an", "the", "my", "your", "his", "her", "its", "our", "their", "this", "these",
               "those", "every", "each"}
# Average glyph width in em, used to cap characters per line before the browser measures.
AVG_EM = {("Inter", False): 0.56, ("Inter", True): 0.68, ("Montserrat", False): 0.62, ("Montserrat", True): 0.78}


@dataclass
class TWord:
    text: str
    start: float
    end: float
    level: float | None
    sentence_end: bool
    clause_end: bool


@dataclass
class Cue:
    words: list[TWord] = field(default_factory=list)
    lines: list[list[int]] = field(default_factory=list)
    start: float = 0.0
    end: float = 0.0
    emphasis: list[int] = field(default_factory=list)
    placement: str = "bottom"
    scrim: bool = False

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def timeline_words(tl: Timeline, words: dict[str, WordsFile | None], fillers: set[str]) -> list[TWord]:
    """Every kept word, in edit order, with timeline times (fillers left out: clean verbatim)."""
    out: list[TWord] = []
    for c in tl.track_clips("V1"):
        wf = words.get(c.source)
        if wf is None:
            continue
        for w in wf.words:
            mid = (w.start + w.end) / 2
            if c.src_in <= mid < c.src_out and w.norm not in fillers:
                out.append(TWord(w.text, round(c.to_timeline(max(w.start, c.src_in)), 3),
                                 round(c.to_timeline(min(w.end, c.src_out)), 3), w.level_db,
                                 w.sentence_end, w.clause_end))
    return out


def resolved_style(style: dict, width: int, height: int) -> dict:
    """Style tokens with relative sizes turned into pixels for this frame size."""
    base = min(width, height)
    s = {k: v for k, v in style.items() if not k.endswith("Rel") and k not in ("description", "max_words", "lines", "box")}
    s["fontSize"] = round(style.get("fontSizeRel", 0.05) * base)
    if style.get("strokeWidthRel"):
        s["strokeWidth"] = max(1, round(style["strokeWidthRel"] * s["fontSize"]))
    if style.get("box"):
        b = style["box"]
        s["box"] = {"color": b["color"], "radius": round(b.get("radiusRel", 0.2) * s["fontSize"]),
                    "padX": round(b.get("padXRel", 0.3) * s["fontSize"]), "padY": round(b.get("padYRel", 0.1) * s["fontSize"]),
                    "gap": b.get("gap", 4)}
    s.setdefault("lineHeight", 1.18)
    return s


def max_chars(style: dict, cfg: dict, width: int, height: int, safe: dict, short: bool) -> int:
    rs = resolved_style(style, width, height)
    em = AVG_EM.get((style.get("fontFamily", "Inter"), bool(style.get("uppercase"))), 0.62)
    usable = (safe["right"] - safe["left"]) * width - 2 * (rs.get("box", {}).get("padX", 0) + rs.get("strokeWidth", 0))
    by_width = int(usable / (rs["fontSize"] * em))
    cap = cfg["max_chars_per_line_short"] if short else cfg["max_chars_per_line"]
    return max(8, min(cap, by_width))


LONG_PAUSE = 1.0   # a pause this long breaks even mid-phrase


def _units(words: list[TWord], cfg: dict) -> list[list[TWord]]:
    """Hard breaks: sentence ends and real pauses start a new caption, except a pause right
    after a word that cannot end a phrase ("…at White Sands during [pause] the last…"): the
    phrase stays together unless the pause is very long."""
    units, cur = [], []
    for w in words:
        gap = w.start - cur[-1].end if cur else 0.0
        dangling = bool(cur) and normalize(cur[-1].text) in WEAK_END and not cur[-1].clause_end
        pause_break = gap >= cfg["break_on_pause"] and (not dangling or gap >= LONG_PAUSE)
        if cur and (cur[-1].sentence_end or pause_break):
            units.append(cur)
            cur = []
        cur.append(w)
    if cur:
        units.append(cur)
    return units


def _proper_pair(a: TWord, b: TWord | None) -> bool:
    """Two capitalised words in a row, mid-sentence: a name ("White Sands", "North America")."""
    return (b is not None and not a.sentence_end and not a.clause_end and a.text[:1].isupper()
            and b.text[:1].isupper() and normalize(a.text) not in {"i"})


def _continues(next_word: TWord | None) -> bool:
    """Does the next word probably continue the phrase (a content word, not "of", "and", ...)?"""
    return next_word is not None and normalize(next_word.text) not in STOPWORDS


def _chunk_cost(chunk: list[TWord], is_unit_end: bool, cap: int, unit_len: int,
                next_word: TWord | None = None) -> float:
    text_len = len(" ".join(w.text for w in chunk))
    cost = 3.0                                                   # fewer captions is calmer
    last = normalize(chunk[-1].text)
    if not is_unit_end and last in WEAK_END:
        cost += 12.0                                             # "…welcome to / the test clip"
    if not is_unit_end and chunk[-1].clause_end:
        cost -= 4.0                                              # commas are natural breaks
    nxt = _continues(next_word)
    if not is_unit_end and nxt and not chunk[-1].clause_end:
        cost += 5.0                                              # prefer breaking before a function word
    if not is_unit_end and _proper_pair(chunk[-1], next_word):
        cost += 10.0                                             # never split "White | Sands"
    if not is_unit_end and nxt and any(ch.isdigit() for ch in chunk[-1].text) and not chunk[-1].clause_end:
        cost += 8.0                                              # "400 | grams": keep a number with its unit
    if (not is_unit_end and nxt and len(chunk) >= 2 and normalize(chunk[-2].text) in DETERMINERS
            and not chunk[-1].clause_end):
        cost += 8.0                                              # "a simple | loaf": noun phrase left open
    if len(chunk) == 1 and unit_len > 1:
        cost += 6.0                                              # lone-word captions flicker
    cost += 4.0 * (1 - min(1.0, text_len / cap)) ** 2            # prefer reasonably full captions
    return cost


def _segment(words: list[TWord], cfg: dict, max_words: int, n_lines: int, line_chars: int) -> list[Cue]:
    """Split each sentence/phrase optimally (dynamic programming) into captions that respect
    word, character and duration limits, avoid dangling words and prefer comma boundaries."""
    cap = n_lines * line_chars
    cues: list[Cue] = []
    for unit in _units(words, cfg):
        n = len(unit)
        best = [0.0] + [float("inf")] * n
        back = [0] * (n + 1)
        for i in range(1, n + 1):
            for j in range(max(0, i - max_words), i):
                chunk = unit[j:i]
                if len(chunk) > 1 and (len(" ".join(w.text for w in chunk)) > cap
                                       or chunk[-1].end - chunk[0].start > cfg["max_duration"]):
                    continue
                c = best[j] + _chunk_cost(chunk, i == n, cap, n, unit[i] if i < n else None)
                if c < best[i]:
                    best[i], back[i] = c, j
        bounds, i = [], n
        while i > 0:
            bounds.append((back[i], i))
            i = back[i]
        cues += [Cue(words=unit[a:b]) for a, b in reversed(bounds)]
    return cues


def _break_lines(c: Cue, n_lines: int, line_chars: int) -> list[list[int]]:
    """Split a caption into balanced lines at good phrase points (never after a weak word)."""
    n = len(c.words)
    if n_lines < 2 or len(c.text) <= line_chars or n < 2:
        return [list(range(n))]
    best, best_cost = None, math.inf
    for k in range(1, n):
        a = " ".join(w.text for w in c.words[:k])
        b = " ".join(w.text for w in c.words[k:])
        cost = abs(len(a) - len(b))
        if len(a) > line_chars or len(b) > line_chars:
            cost += 100 + 5 * max(len(a), len(b))
        last = c.words[k - 1]
        nxt = _continues(c.words[k])
        if normalize(last.text) in WEAK_END:
            cost += 40
        if nxt and any(ch.isdigit() for ch in last.text) and not last.clause_end:
            cost += 40                      # never "220 / degrees"
        if nxt and k >= 2 and normalize(c.words[k - 2].text) in DETERMINERS and not last.clause_end:
            cost += 25                      # never "a simple / loaf"
        if nxt and not last.clause_end:
            cost += 12                      # break before function words ("…say / that…"), not inside phrases
        if _proper_pair(last, c.words[k]):
            cost += 40                      # never "White / Sands"
        if last.clause_end:
            cost -= 15
        if cost < best_cost:
            best, best_cost = k, cost
    return [list(range(best)), list(range(best, n))]


def _choose_emphasis(c: Cue) -> list[int]:
    """At most one emphasised word: a number, else a clearly stressed content word."""
    for i, w in enumerate(c.words):
        if any(ch.isdigit() for ch in w.text):
            return [i]
    levels = [w.level for w in c.words if w.level is not None]
    if len(levels) < 3:
        return []
    med = sorted(levels)[len(levels) // 2]
    best = None
    for i, w in enumerate(c.words):
        tok = normalize(w.text)
        if w.level is None or tok in STOPWORDS or len(tok) < 4:
            continue
        lift = w.level - med
        if lift >= 3.0 and (best is None or lift > best[1]):
            best = (i, lift)
    return [best[0]] if best else []


def plan_captions(tl: Timeline, words: dict[str, WordsFile | None], style_doc: dict, style_name: str,
                  *, output: str = "youtube_long", width: int | None = None, height: int | None = None,
                  faces=None, brightness=None) -> Captions:
    """Plan every caption cue. ``faces(t0, t1)`` → list of face boxes and ``brightness(t, y0, y1)``
    → luminance are optional callbacks (placement away from faces, automatic scrims)."""
    cfg = style_doc["captions"]
    if style_name not in cfg["styles"]:
        raise KeyError(f"unknown caption style '{style_name}'; known: {', '.join(cfg['styles'])}")
    style = cfg["styles"][style_name]
    width, height = width or tl.meta.width, height or tl.meta.height
    short = output == "youtube_short"
    safe = cfg["safe_zones"][output]
    fillers = {normalize(f) for f in style_doc["editing"]["fillers_always"]}
    tw = timeline_words(tl, words, fillers)
    for k, w in enumerate(tw):
        # A sentence that started with a removed filler ("Um, we found…") still starts with a capital.
        if (k == 0 or tw[k - 1].sentence_end) and w.text[:1].islower():
            w.text = w.text[:1].upper() + w.text[1:]
    n_lines = min(style.get("lines", 2), cfg["max_lines"])
    line_chars = max_chars(style, cfg, width, height, safe, short)
    raw = _segment(tw, cfg, style.get("max_words", 10), n_lines, line_chars)
    fps = tl.meta.fps
    rs = resolved_style(style, width, height)
    for k, c in enumerate(raw):
        c.lines = _break_lines(c, n_lines, line_chars)
        c.start = max(0.0, c.words[0].start - cfg["lead_frames"] / fps)
        nxt = raw[k + 1].words[0].start if k + 1 < len(raw) else tl.duration
        end = c.words[-1].end + cfg["linger"]
        if end - c.start < cfg["min_duration"]:
            end = c.start + cfg["min_duration"]
        c.end = round(min(end, nxt - 1.0 / fps, tl.duration), 3)
        c.end = max(c.end, c.words[-1].end)
        if cfg.get("emphasis") and (style.get("emphasisColor") or style.get("emphasisScale")):
            c.emphasis = _choose_emphasis(c)
        block_h = len(c.lines) * rs["fontSize"] * rs["lineHeight"] / height + 0.02
        band = (safe["bottom"] - block_h, safe["bottom"])
        if faces is not None:
            for f in faces(c.start, c.end):
                mouth_y = max(f["mouth"][1], f["mouth"][3]) if f.get("mouth") else f["y"] + f["h"] * 0.8
                if band[0] < mouth_y + 0.03 and f["y"] < band[1]:
                    c.placement = "top"
                    break
        if brightness is not None and not style.get("box") and not style.get("strokeWidthRel"):
            mid = (c.start + c.end) / 2
            y0, y1 = (band if c.placement == "bottom" else (safe["top"], safe["top"] + block_h))
            lum = brightness(mid, y0, y1)
            c.scrim = lum is not None and lum > cfg["scrim_threshold"]
    cues = [CaptionCue(start=round(c.start, 3), end=round(c.end, 3), text=c.text,
                       words=[{"text": w.text, "start": w.start, "end": w.end} for w in c.words],
                       lines=c.lines, emphasis=c.emphasis, placement=c.placement, scrim=c.scrim) for c in raw]
    return Captions(style=style_name, safe_zone=output, max_lines=n_lines, cues=cues, stats=caption_stats(cues, cfg))


def caption_stats(cues: list[CaptionCue], cfg: dict) -> dict:
    if not cues:
        return {"cues": 0}
    cps = [len(c.text) / max(c.end - c.start, 1e-3) for c in cues]
    return {"cues": len(cues), "max_cps": round(max(cps), 1), "median_cps": round(sorted(cps)[len(cps) // 2], 1),
            "over_target_cps": sum(x > cfg["max_cps"] for x in cps),
            "over_hard_cps": sum(x > cfg["hard_max_cps"] for x in cps),
            "min_duration_s": round(min(c.end - c.start for c in cues), 2),
            "max_line_chars": max(len(" ".join(c.words[i]["text"] for i in line)) for c in cues for line in c.lines),
            "placed_top": sum(c.placement == "top" for c in cues), "scrims": sum(c.scrim for c in cues),
            "emphasised": sum(bool(c.emphasis) for c in cues)}


def to_remotion_props(caps: Captions, style_doc: dict, *, width: int, height: int, fps: float,
                      duration: float) -> dict:
    """Frame-based props for graphics/src/components/Captions.jsx."""
    cfg = style_doc["captions"]
    style = resolved_style(cfg["styles"][caps.style], width, height)
    style["scrimColor"] = "rgba(0,0,0,0.42)"
    cues = []
    for i, c in enumerate(caps.cues):
        f0, f1 = int(math.floor(c.start * fps + 1e-6)), int(math.ceil(c.end * fps - 1e-6))
        lines = [[{"text": c.words[j]["text"], "from": int(round(c.words[j]["start"] * fps)),
                   "to": int(round(c.words[j]["end"] * fps)), "emph": j in c.emphasis} for j in line] for line in c.lines]
        cues.append({"id": f"q{i:04d}", "from": f0, "to": max(f1, f0 + 1), "placement": c.placement,
                     "scrim": c.scrim, "lines": lines})
    for a, b in zip(cues, cues[1:]):   # never two captions on screen at once
        a["to"] = min(a["to"], b["from"])
    return {"width": width, "height": height, "fps": fps, "durationInFrames": int(math.ceil(duration * fps)),
            "style": style, "safe": cfg["safe_zones"][caps.safe_zone], "cues": cues}
