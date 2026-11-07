"""Facts on screen must come from sources (EDITING_STANDARDS §7.6).

Every number a graphic shows is checked against its references: a transcript span (what the
speaker said) or a document the client provided (job-relative path, or the brief). Spoken and
written forms compare equal ("one point five million" = "1.5 million" = 1,500,000). A number
that cannot be found is refused. Words are checked more loosely: content words missing from
the references are reported (paraphrase is allowed; invention is not, and the review sees it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cutroom.errors import RefusedError

_UNITS = {w: i for i, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                                       "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
                                       "sixteen", "seventeen", "eighteen", "nineteen"])}
_TENS = {w: 10 * i for i, w in enumerate(["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
                                           "eighty", "ninety"]) if w}
_SCALES = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_SUFFIX_SCALE = {"k": 1e3, "m": 1e6, "mm": 1e6, "b": 1e9, "bn": 1e9, "t": 1e12}
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
        "ninth": 9, "tenth": 10, "twelfth": 12, "twentieth": 20, "hundredth": 100}
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "by", "is", "are", "was",
         "were", "be", "it", "this", "that", "these", "those", "you", "your", "we", "our", "i", "my", "how",
         "what", "why", "who", "vs", "versus", "step", "part", "chapter"}


def _tokens(text: str) -> list[str]:
    text = text.lower().replace("’", "'").replace("—", " ").replace("–", " ")
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    text = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text)        # twenty-five
    return re.findall(r"\$?\d+(?:\.\d+)?(?:%|[a-z]{1,2}\b)?|[a-z']+", text)


def numbers_in(text: str) -> list[float]:
    """Every number written or spoken in ``text``. "1.5 million" yields 1.5e6 and 1.5 (the
    mantissa a graphic may show with an M suffix); "fifty percent" yields 50."""
    toks = _tokens(text)
    out: list[float] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        m = re.fullmatch(r"\$?(\d+(?:\.\d+)?)(%|[a-z]{1,2})?", t)
        if m:
            v = float(m.group(1))
            suf = m.group(2) or ""
            out.append(v)
            if suf in _SUFFIX_SCALE:
                out.append(v * _SUFFIX_SCALE[suf])
            elif i + 1 < len(toks) and toks[i + 1] in _SCALES:
                out.append(v * _SCALES[toks[i + 1]])
                i += 1
            i += 1
            continue
        if t in _ORD:
            out.append(float(_ORD[t]))
            i += 1
            continue
        if t in _UNITS or t in _TENS or (t == "a" and i + 1 < len(toks) and toks[i + 1] in ("hundred", *_SCALES)):
            vals, j = _spoken(toks, i)
            out += vals
            i = max(j, i + 1)
            continue
        i += 1
    return out


def _kind(w: str) -> str:
    if w in _UNITS:
        v = _UNITS[w]
        return "unit" if v < 10 else "teen"
    return "tens" if w in _TENS else ("hundred" if w == "hundred" else "scale")


# Which number word may follow which: "twenty three" yes, "nineteen ninety" starts a new group.
_FOLLOWS = {None: {"unit", "teen", "tens"}, "unit": {"hundred", "scale"}, "teen": {"hundred", "scale"},
            "tens": {"unit", "hundred", "scale"}, "hundred": {"unit", "teen", "tens", "scale"},
            "scale": {"unit", "teen", "tens"}}


def _spoken(toks: list[str], i: int) -> tuple[list[float], int]:
    """Parse spoken number words from toks[i]; returns (values, next index). Groups that cannot
    combine ("nineteen ninety eight") are separate numbers, and two such groups also read as a year."""
    groups: list[float] = []
    total = cur = 0.0
    last = None
    j = i
    frac_out: list[float] = []
    while j < len(toks):
        w = toks[j]
        if w == "a" and last is None and j + 1 < len(toks) and toks[j + 1] in ("hundred", *_SCALES):
            cur, last = 1.0, "unit"
            j += 1
            continue
        if w == "and" and last in ("hundred", "scale") and j + 1 < len(toks) and (toks[j + 1] in _UNITS or toks[j + 1] in _TENS):
            j += 1
            continue
        if w == "point" and last is not None and j + 1 < len(toks) and toks[j + 1] in _UNITS:
            digits = []
            while j + 1 < len(toks) and toks[j + 1] in _UNITS and _UNITS[toks[j + 1]] < 10:
                j += 1
                digits.append(str(_UNITS[toks[j]]))
            v = total + cur + float("0." + "".join(digits))
            j += 1
            if j < len(toks) and toks[j] in _SCALES:
                frac_out += [v, v * _SCALES[toks[j]]]
                j += 1
            else:
                frac_out.append(v)
            return groups + frac_out, j
        if not (w in _UNITS or w in _TENS or w == "hundred" or w in _SCALES):
            break
        k = _kind(w)
        if k not in _FOLLOWS[last]:
            if last is None:
                break
            groups.append(total + cur)          # start a new group
            total = cur = 0.0
            last = None
            if k not in _FOLLOWS[None]:
                break
        if k in ("unit", "teen"):
            cur += _UNITS[w]
        elif k == "tens":
            cur += _TENS[w]
        elif k == "hundred":
            cur = max(cur, 1) * 100
        else:
            total += max(cur, 1) * _SCALES[w]
            cur = 0.0
        last = k
        j += 1
    if last is not None:
        groups.append(total + cur)
    out = list(groups)
    for a, b in zip(groups, groups[1:]):
        if 10 <= a < 100 and b < 100:
            out.append(a * 100 + b)               # "nineteen ninety-eight" = 1998
    return out, j


def content_words(text: str) -> set[str]:
    return {t for t in _tokens(text) if not re.search(r"\d", t) and t not in _STOP and len(t) > 2
            and t not in _UNITS and t not in _TENS}


def _decimals(v: float) -> int:
    s = repr(float(v))
    return 0 if s.endswith(".0") else len(s.split(".")[1])


def supported(v: float, refs: list[float]) -> bool:
    """A shown number is supported if a reference number rounds to it at the shown precision."""
    tol = 0.5 * 10 ** -_decimals(v) + 1e-9
    return any(abs(v - r) <= tol for r in refs)


def shown_numbers(spec, params: dict) -> list[tuple[str, float]]:
    """(parameter, value) for every number the graphic will display."""
    out = []
    for k, p in spec.params.items():
        v = params.get(k)
        if v is None or p["type"] in ("rect", "color", "enum", "asset"):
            continue
        if p["type"] in ("number", "integer"):
            if k == "from" and float(v) == 0:      # a counter's animation start, not a claim
                continue
            if k in ("decimals", "step", "number", "opacity"):
                continue
            out.append((k, float(v)))
        elif p["type"] == "data":
            out += [(f"{k}[{d['label']}]", float(d["value"])) for d in v]
            out += [(f"{k}[label]", x) for d in v for x in numbers_in(d["label"])]
        elif p["type"] == "string":
            out += [(k, x) for x in numbers_in(v)]
        elif p["type"] == "list":
            out += [(k, x) for s in v for x in numbers_in(_strip_marker(s))]
        elif p["type"] == "column":
            out += [(k, x) for s in [v.get("title", ""), *v.get("items", [])] for x in numbers_in(s)]
    return out


def _strip_marker(s: str) -> str:
    return re.sub(r"^\s*(\d+[.)]|step \d+:?)\s*", "", s, flags=re.I)


@dataclass
class FactCheck:
    references: list[str]
    numbers: list[dict] = field(default_factory=list)
    unsupported_numbers: list[dict] = field(default_factory=list)
    unsupported_words: list[str] = field(default_factory=list)
    word_support: float | None = None

    @property
    def ok(self) -> bool:
        return not self.unsupported_numbers

    def as_dict(self) -> dict:
        return {"ok": self.ok, "references": self.references, "numbers": self.numbers,
                "unsupported_numbers": self.unsupported_numbers, "unsupported_words": self.unsupported_words,
                "word_support": self.word_support}


def check_facts(spec, params: dict, refs: dict[str, str]) -> FactCheck:
    """``refs`` maps a reference id (``transcript:S01:12.30-15.80``, ``doc:inputs/x.md``) to its text."""
    fc = FactCheck(references=list(refs))
    pool: dict[str, list[float]] = {r: numbers_in(t) for r, t in refs.items()}
    for k, v in shown_numbers(spec, params):
        where = [r for r, nums in pool.items() if supported(v, nums)]
        if where:
            fc.numbers.append({"param": k, "value": v, "found_in": where[0]})
        else:
            fc.unsupported_numbers.append({"param": k, "value": v})
    words: set[str] = set()
    for k in spec.text_params:
        v = params.get(k)
        if isinstance(v, str):
            words |= content_words(v)
        elif isinstance(v, list):
            for x in v:
                words |= content_words(str(x["label"]) if isinstance(x, dict) else _strip_marker(str(x)))
        elif isinstance(v, dict):
            words |= content_words(" ".join([v.get("title", ""), *v.get("items", [])]))
    if words:
        have = set().union(*(content_words(t) for t in refs.values())) if refs else set()
        stems = {w[:5] for w in have}
        missing = sorted(w for w in words if w not in have and w[:5] not in stems)
        fc.unsupported_words = missing
        fc.word_support = round(1 - len(missing) / len(words), 2)
    return fc


def require_facts(spec, params: dict, refs: dict[str, str]) -> FactCheck:
    """Refuse a graphic whose numbers are not in its references."""
    shown = shown_numbers(spec, params)
    if shown and not refs:
        raise RefusedError(f"This {spec.id} shows numbers ({', '.join(f'{v:g}' for _, v in shown)}) but has no source.",
                           fix="Add --fact with the transcript span that says it (e.g. --fact 'sentences:12' or "
                               "--fact 'S01@83.2-90.5') or a document you were given (--fact doc:inputs/stats.md).")
    fc = check_facts(spec, params, refs)
    if not fc.ok:
        bad = ", ".join(f"{d['value']:g} ({d['param']})" for d in fc.unsupported_numbers)
        raise RefusedError(f"Number(s) not found in the references: {bad}. Graphics never show invented figures.",
                           fix="Point --fact at the span or document that states the number, or show what was said.")
    return fc
