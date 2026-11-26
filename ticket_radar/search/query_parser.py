"""Parse the user's search box text into SQLite FTS5 expressions.

Supported syntax:
  word            prefix match ("redis" also matches "redis-prd-01")
  "exact phrase"  phrase match
  -word           exclude
  INC0012345      ticket-number lookup (handled by the engine)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .hebrew import query_variants

_TOKEN = re.compile(r'-?"[^"]*"|\S+')
_TICKET_NUMBER = re.compile(r"[A-Za-z]{2,8}\d{3,}")
_WORD_CHAR = re.compile(r"\w")


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    include: tuple[str, ...] = ()        # FTS5 sub-expressions
    exclude: tuple[str, ...] = ()
    words: tuple[str, ...] = field(default=())  # plain words (for LIKE fallback)

    @property
    def empty(self) -> bool:
        return not self.include

    def _with_exclusions(self, core: str) -> str:
        return f"({core})" + "".join(f" NOT {e}" for e in self.exclude)

    def match_all(self) -> str:
        return self._with_exclusions(" AND ".join(self.include))

    def match_any(self) -> str:
        return self._with_exclusions(" OR ".join(self.include))

    @property
    def single_term(self) -> bool:
        return len(self.include) == 1


def is_ticket_number(text: str) -> bool:
    return bool(_TICKET_NUMBER.fullmatch(text.strip()))


def _quote(text: str) -> str:
    return '"' + text.replace('"', " ").strip() + '"'


def _term(token: str) -> tuple[str, str] | None:
    """Return (fts_expression, plain_word) for a single token, or None if it has no word chars."""
    if len(token) >= 2 and token[0] == token[-1] == '"':
        inner = token[1:-1].replace('"', " ").strip()
        if not _WORD_CHAR.search(inner):
            return None
        return _quote(inner), inner
    inner = token.replace('"', " ").strip()
    if not _WORD_CHAR.search(inner):
        return None
    variants = query_variants(inner)
    exprs = [_quote(v) + ("*" if len(v) >= 2 else "") for v in variants]
    expr = exprs[0] if len(exprs) == 1 else "(" + " OR ".join(exprs) + ")"
    return expr, inner


def parse(text: str) -> ParsedQuery:
    include: list[str] = []
    exclude: list[str] = []
    words: list[str] = []
    for token in _TOKEN.findall(text or ""):
        negative = token.startswith("-") and len(token) > 1
        parsed = _term(token[1:] if negative else token)
        if parsed is None:
            continue
        expr, word = parsed
        if negative:
            exclude.append(expr)
        else:
            include.append(expr)
            words.append(word)
    return ParsedQuery(tuple(include), tuple(exclude), tuple(words))
