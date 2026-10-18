"""Text helpers: Turkish-aware lowercasing, sentence splitting, number extraction."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_ABBREVIATIONS = {
    "u.s",
    "u.k",
    "inc",
    "corp",
    "co",
    "ltd",
    "mr",
    "mrs",
    "ms",
    "dr",
    "st",
    "vs",
    "jr",
    "no",
    "prof",
    "gen",
    "sen",
    "rep",
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "sept",
    "oct",
    "nov",
    "dec",
    "e.g",
    "i.e",
    "a.ş",
    "vb",
    "örn",
}
_SENT_END_RE = re.compile(r"([.!?…]+[\"'”’)]*)\s+")
_TERMINATED_RE = re.compile(r"[.!?…][\"'”’)]*$")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


def tr_lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def is_terminated(text: str) -> bool:
    """True if the text ends with sentence-final punctuation (i.e. it is not cut mid-sentence)."""
    return bool(_TERMINATED_RE.search(text.strip()))


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    sentences: list[str] = []
    start = 0
    for m in _SENT_END_RE.finditer(text):
        end = m.end(1)
        chunk = text[start:end]
        last_word = chunk.rsplit(" ", 1)[-1].rstrip(".!?…\"'”’)").lower()
        nxt = text[m.end() : m.end() + 1]
        if last_word in _ABBREVIATIONS or (len(last_word) == 1 and last_word.isalpha()):
            continue
        if nxt and not (nxt.isupper() or nxt.isdigit() or nxt in "\"'“‘("):
            continue
        sentences.append(chunk.strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def first_sentences(text: str, max_chars: int, max_sentences: int = 2) -> str:
    """Whole sentences only, within max_chars. Returns "" rather than cutting a sentence."""
    out: list[str] = []
    for sentence in split_sentences(text)[:max_sentences]:
        if not is_terminated(sentence):
            break
        candidate = " ".join([*out, sentence])
        if len(candidate) > max_chars:
            break
        out.append(sentence)
    return " ".join(out)


def _parse_grouped(token: str, thousands: str, decimal: str) -> Decimal | None:
    if token.count(decimal) > 1:
        return None
    int_part, _, frac = token.partition(decimal)
    groups = int_part.split(thousands)
    bad_groups = not groups[0] or len(groups[0]) > 3 or any(len(g) != 3 for g in groups[1:])
    if len(groups) > 1 and bad_groups:
        return None
    if thousands in frac:
        return None
    try:
        return Decimal("".join(groups) + ("." + frac if frac else ""))
    except InvalidOperation:
        return None


def number_values(token: str) -> set[Decimal]:
    """All plausible numeric values of a token under US (1,234.5) and TR (1.234,5) notation."""
    values = set()
    for thousands, decimal in ((",", "."), (".", ",")):
        v = _parse_grouped(token, thousands, decimal)
        if v is not None:
            values.add(v.normalize())
    return values


def extract_numbers(text: str) -> list[str]:
    return _NUMBER_RE.findall(text)


def numbers_not_in(output: str, source: str) -> list[str]:
    """Numbers in `output` whose value does not appear anywhere in `source`."""
    available: set[Decimal] = set()
    for token in extract_numbers(source):
        available |= number_values(token)
    return [t for t in extract_numbers(output) if not (number_values(t) & available)]
