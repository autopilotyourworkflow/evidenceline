"""Questions to try instead, when the question box cannot answer: the ones closest to what was asked.

The pool is ``data/suggested_questions.json``: plain questions, each known to find the right section of the indexed
guidance. They are ranked by the words they share with the question, a shared word counting more the fewer questions
in the pool contain it (so 'groundwater' outweighs 'site'). When nothing is shared, the file's default questions are
offered. No model is called.
"""

from __future__ import annotations

import json
import re
from functools import cache
from importlib import resources

HOW_MANY = 3
_WORD = re.compile(r"[a-z]+")
_COMMON = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "do",
        "does",
        "for",
        "from",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "should",
        "the",
        "to",
        "us",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
        "this",
        "that",
        "there",
        "their",
        "them",
        "they",
        "about",
        "any",
        "some",
        "into",
    ]
)


def _stem(word: str) -> str:
    """A rough stem, enough to match 'samples' with 'sample' and 'reporting' with 'report'."""
    for ending in ("ing", "ed", "es", "s"):
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            return word[: -len(ending)]
    return word


def _stems(text: str) -> frozenset[str]:
    return frozenset(_stem(w) for w in _WORD.findall(text.lower()) if w not in _COMMON and len(w) >= 3)


def _same(a: str, b: str) -> bool:
    """The same word, or one starts the other and both have at least 5 letters ('audit', 'auditor')."""
    return a == b or (min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a)))


@cache
def _pool() -> tuple[tuple[str, ...], int]:
    raw = json.loads((resources.files("evidenceline") / "data" / "suggested_questions.json").read_text("utf-8"))
    return tuple(raw["questions"]), int(raw["defaults"])


def suggestions(question: str) -> list[str]:
    """Up to :data:`HOW_MANY` questions to try instead, closest to ``question`` first; never ``question`` itself."""
    pool, defaults = _pool()
    asked = question.strip().lower()
    candidates = [q for q in pool if q.lower() != asked]
    stems = [_stems(q) for q in candidates]
    rarity = {s: 1 / sum(s in found for found in stems) for found in stems for s in found}
    wanted = _stems(question)
    scores = [sum(rarity[s] for s in found if any(_same(s, w) for w in wanted)) for found in stems]
    order = sorted(range(len(candidates)), key=lambda i: (-scores[i], i))
    picked = [candidates[i] for i in order if scores[i] > 0][:HOW_MANY]
    for q in candidates[:defaults] + candidates:
        if len(picked) == HOW_MANY:
            break
        if q not in picked:
            picked.append(q)
    return picked
