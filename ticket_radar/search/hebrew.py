"""Light-weight Hebrew normalisation for full-text search.

Hebrew attaches prepositions/conjunctions to the word ("לשרתים" = "to the servers") and inflects
with suffixes, so a plain tokenizer won't match "שרת" against "לשרתים". We index extra forms of
each document word (prefix-stripped and suffix-stripped) and expand query words the same way.
"""
from __future__ import annotations

import re

PREFIX_LETTERS = frozenset("והבלמשכ")
SUFFIXES = ("ים", "ות", "ה", "ת", "י")
MIN_STEM = 3

_HEBREW_WORD = re.compile(r"[א-ת]{4,}")


def is_hebrew(text: str) -> bool:
    return any("֐" <= ch <= "׿" for ch in text)


def _strip_suffixes(word: str) -> set[str]:
    return {word[: -len(s)] for s in SUFFIXES if word.endswith(s) and len(word) - len(s) >= MIN_STEM}


def document_forms(*texts: str | None) -> str:
    """Extra searchable forms for all Hebrew words in `texts` (space separated, de-duplicated)."""
    forms: set[str] = set()
    for word in _HEBREW_WORD.findall(" ".join(t for t in texts if t)):
        bases = [word]
        if word[0] in PREFIX_LETTERS:
            bases.append(word[1:])
            if len(word) >= 5 and word[1] in PREFIX_LETTERS:
                bases.append(word[2:])
        for base in bases:
            if len(base) >= MIN_STEM:
                forms.add(base)
            forms |= _strip_suffixes(base)
    return " ".join(sorted(forms))


def query_variants(word: str) -> list[str]:
    """Forms of a query word to OR together. Only strips a prefix from longer words, because
    for short ones the first letter is usually part of the root (e.g. "בקשה")."""
    if not is_hebrew(word) or " " in word or len(word) <= 3:
        return [word]
    bases = [word]
    if word[0] in PREFIX_LETTERS and len(word) >= 5:
        bases.append(word[1:])
    variants = set(bases)
    for base in bases:
        variants |= _strip_suffixes(base)
    return sorted(variants, key=len, reverse=True)
