"""English text normalisation and word error rate, for verifying transcripts.

Normalisation makes "400 grams," and "four hundred grams" compare equal, so a
WER figure reflects recognition errors rather than formatting choices.
"""

from __future__ import annotations

import re

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
         "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = [(10**9, "billion"), (10**6, "million"), (1000, "thousand"), (100, "hundred")]


def int_to_words(n: int) -> str:
    """Spell a non-negative integer the way English speakers say it (no 'and')."""
    if n < 0:
        return "minus " + int_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + ("" if ones == 0 else " " + _ONES[ones])
    for value, word in _SCALES:
        if n >= value:
            head, rest = divmod(n, value)
            out = f"{int_to_words(head)} {word}"
            return out if rest == 0 else f"{out} {int_to_words(rest)}"
    raise AssertionError("unreachable")


_ORDINAL_SUFFIX = re.compile(r"^(\d+)(st|nd|rd|th)$")
_ORDINALS = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
             "nine": "ninth", "twelve": "twelfth"}


def _ordinal(words: str) -> str:
    *head, last = words.split()
    if last in _ORDINALS:
        last = _ORDINALS[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return " ".join([*head, last])


def _number_token(tok: str) -> str:
    tok = tok.replace(",", "")
    m = _ORDINAL_SUFFIX.match(tok)
    if m:
        return _ordinal(int_to_words(int(m.group(1))))
    if tok.endswith("%") and tok[:-1].isdigit():
        return int_to_words(int(tok[:-1])) + " percent"
    if tok.startswith("$") and tok[1:].isdigit():
        return int_to_words(int(tok[1:])) + " dollars"
    if re.fullmatch(r"\d+\.\d+", tok):
        whole, frac = tok.split(".")
        return int_to_words(int(whole)) + " point " + " ".join(_ONES[int(d)] for d in frac)
    if tok.isdigit():
        return int_to_words(int(tok))
    return tok


def normalize(text: str) -> str:
    """Lower-case, strip punctuation, spell out numbers, collapse whitespace."""
    text = text.lower().replace("’", "'").replace("–", " ").replace("—", " ")
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)            # 1,000 -> 1000
    text = re.sub(r"[^\w\s'%$.\-]", " ", text)
    tokens = []
    for tok in text.replace("-", " ").split():
        tok = tok.strip(".'")
        if not tok:
            continue
        tokens.append(_number_token(tok) if re.search(r"\d", tok) else tok)
    return " ".join(" ".join(tokens).split())


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over normalised words, divided by reference length."""
    ref = normalize(reference).split()
    hyp = normalize(hypothesis).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)
