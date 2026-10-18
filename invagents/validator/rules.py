"""Shared validation rules: forbidden commentary/advice language, numbers-in-input."""

from __future__ import annotations

import re

from invagents.textutil import numbers_not_in, tr_lower

# Forecast / commentary language (owner's list + close variants). Regex on Turkish-lowercased text.
FORECAST_PATTERNS: dict[str, str] = {
    "beklenebilir": r"\bbeklenebil",
    "olabilir": r"\bolabil",
    "işaret ediyor": r"\bişaret ed",
    "sert": r"\bsert(?:çe|leşe\w*)?\b",
    "güçlü": r"\bgüçlü\w*",
    "endişe verici": r"\bendişe verici",
    "muhtemelen": r"\bmuhtemel\w*",
    "tahmin": r"\btahmin(?:imiz|imce|en)\b",
    "fiyat tahmini": (
        r"\b(?:yükselecek|düşecek|artacak|azalacak|gerileyecek|toparlanacak|çökecek)\w*"
    ),
    "kesin": r"\bkesinlikle\b",
    "dikkat çekici": r"\bdikkat çekici",
    "korkutucu": r"\bkorkutucu",
}

# Investment-advice language (rule 9).
ADVICE_PATTERNS: dict[str, str] = {
    "al/sat/tut": r"\b(?:almalı|satmalı|tutmalı|alınmalı|satılmalı|tutulmalı)\w*",
    "al/sat sinyali": r"\b(?:al|sat|alım|satım|satış) (?:sinyali|fırsatı|zamanı)\b",
    "fırsat": r"\bfırsat\w*",
    "tavsiye": r"\btavsiye\w*",
    "öneri": r"\böneri\w*|\böneriyor\w*|\bönerilir\b",
    "hedef fiyat": r"\bhedef fiyat\w*",
    "pozisyon al": r"\bpozisyon (?:al|aç)\w*",
    "kaçırma": r"\bkaçırma\w*",
    "buy/sell": r"\b(?:buy|sell|hold)\b",
}


def find_forbidden(text: str) -> list[str]:
    low = tr_lower(text)
    hits: list[str] = []
    for label, pattern in {**FORECAST_PATTERNS, **ADVICE_PATTERNS}.items():
        if re.search(pattern, low):
            hits.append(label)
    return hits


def unsupported_numbers(output: str, source: str) -> list[str]:
    return numbers_not_in(output, source)
