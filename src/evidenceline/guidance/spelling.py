"""Correct a misspelt question to the words the indexed guidance uses: 'waht is the pfos limt' becomes 'what is the
pfos limit'.

Used only as a second try, by the question box, when the question as typed found nothing
(:mod:`evidenceline.answer.pipeline`); a question that already finds passages is never changed. A word is corrected
only when all of these hold:

- no indexed passage contains it, and it has at least :data:`MIN_LENGTH` letters;
- it is written in lower case or with only a first capital, so an acronym or a name ('RAAF', 'Kwinana') is left alone;
- a word the guidance uses at least :data:`MIN_USES` times starts with the same letter and is within one edit (two
  for a word of :data:`LONG_WORD` letters or more), counting two swapped neighbouring letters as one edit.

The closest such word wins, then the more frequent one. Placeholders put in by redaction ('[CLIENT-1]') and anything
with a digit are left alone.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import cache

MIN_LENGTH = 4
LONG_WORD = 7
MIN_USES = 2
_TOKEN = re.compile(r"\[[^\]]*\]|[A-Za-z0-9][A-Za-z0-9']*")


def _edit_distance(a: str, b: str, limit: int) -> int:
    """Edits (insert, delete, substitute, or swap two neighbouring letters) from a to b; limit + 1 when over it."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    before: list[int] | None = None
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if before is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                current[j] = min(current[j], before[j - 2] + 1)
        if min(current) > limit:
            return limit + 1
        before, previous = previous, current
    return previous[-1]


@cache
def _by_first_letter(words: tuple[tuple[str, int], ...]) -> dict[str, tuple[tuple[str, int], ...]]:
    grouped: dict[str, list[tuple[str, int]]] = {}
    for word, uses in words:
        if uses >= MIN_USES and len(word) >= MIN_LENGTH - 1:
            grouped.setdefault(word[0], []).append((word, uses))
    return {letter: tuple(items) for letter, items in grouped.items()}


def _closest(word: str, candidates: tuple[tuple[str, int], ...]) -> str | None:
    limit = 2 if len(word) >= LONG_WORD else 1
    best: tuple[int, int, str] | None = None
    for known, uses in candidates:
        distance = _edit_distance(word, known, limit)
        if distance <= limit and (best is None or (distance, -uses, known) < best):
            best = (distance, -uses, known)
    return None if best is None else best[2]


def _eligible(token: str, words: Counter[str]) -> bool:
    lower = token.lower()
    return (
        token.isalpha()
        and len(token) >= MIN_LENGTH
        and lower not in words
        and (token == lower or token == lower.capitalize())
    )


def corrected(question: str, words: Counter[str]) -> str | None:
    """The question with each misspelt word replaced by the closest word the guidance uses, or None when no word
    changes. A corrected word keeps a first capital if the typed one had it."""
    groups = _by_first_letter(tuple(sorted(words.items())))
    changed = False
    pieces: list[str] = []
    last = 0
    for match in _TOKEN.finditer(question):
        token = match.group(0)
        replacement = token
        if _eligible(token, words):
            found = _closest(token.lower(), groups.get(token[0].lower(), ()))
            if found is not None:
                replacement = found.capitalize() if token[0].isupper() else found
                changed = True
        pieces.append(question[last : match.start()] + replacement)
        last = match.end()
    pieces.append(question[last:])
    return "".join(pieces) if changed else None
