"""Questions to try instead, when the question box cannot answer: the ones closest to what was asked.

The pool is ``data/suggested_questions.json``: plain questions, each known to find the right section of the indexed
guidance. The question and each pool question are read into the same concepts the search uses
(:func:`evidenceline.guidance.synonyms.query_concepts`), so 'SAQP' meets 'sampling and analysis quality plan', 'HIL'
meets 'health investigation levels' and 'dirt' meets 'soil'. A shared concept counts twice as much as a shared word,
and either counts more the fewer pool questions hold it (so 'groundwater' outweighs 'site'). Words that say what
kind of thing is wanted rather than its subject ('limits', 'rules', 'include') count for nothing, so a soil question
gets the soil question first, not the drinking-water one. So do 'site' (nearly every pool question is about a site),
'plan' and 'model' ('a business plan', 'which model is this?'). When nothing is shared, the file's default questions are
offered. No model is called.
"""

from __future__ import annotations

import json
import re
from functools import cache
from importlib import resources

from evidenceline.guidance.synonyms import query_concepts

HOW_MANY = 3
CONCEPT_WEIGHT = 2.0
_WORD = re.compile(r"[a-z]+")
_GENERIC = frozenset(
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
        "limit",
        "limits",
        "level",
        "levels",
        "value",
        "values",
        "criteria",
        "criterion",
        "standard",
        "standards",
        "guideline",
        "guidelines",
        "guidance",
        "rule",
        "rules",
        "requirement",
        "requirements",
        "required",
        "need",
        "needed",
        "include",
        "includes",
        "cover",
        "covers",
        "check",
        "checks",
        "collect",
        "collected",
        "category",
        "categories",
        "information",
        "info",
        "say",
        "says",
        "mean",
        "means",
        "tell",
        "know",
        "plan",
        "model",
        "site",
        "sites",
    ]
)


def _stem(word: str) -> str:
    """A rough stem, enough to match 'samples' with 'sample' and 'reporting' with 'report'."""
    for ending in ("ing", "ed", "es", "s"):
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            return word[: -len(ending)]
    return word


def _units(text: str) -> frozenset[str]:
    """The question's concepts (as 'c:label') and their words (as 'w:stem'), without the generic ones."""
    units: set[str] = set()
    for concept in query_concepts(text):
        label = concept.label.lower()
        words = [w for w in _WORD.findall(label) if w not in _GENERIC and len(w) >= 3]
        if not words:
            continue
        units.add("c:" + label)
        units.update("w:" + _stem(w) for w in words)
    return frozenset(units)


def _same(a: str, b: str) -> bool:
    """The same unit, or two words where one starts the other, both have at least 5 letters and the longer adds at
    most 3 ('audit', 'auditor'). 'Invest' is not 'investigation'."""
    if a == b:
        return True
    if not (a.startswith("w:") and b.startswith("w:")):
        return False
    x, y = a[2:], b[2:]
    return min(len(x), len(y)) >= 5 and abs(len(x) - len(y)) <= 3 and (x.startswith(y) or y.startswith(x))


@cache
def _pool() -> tuple[tuple[str, ...], int]:
    raw = json.loads((resources.files("evidenceline") / "data" / "suggested_questions.json").read_text("utf-8"))
    return tuple(str(item["question"]) for item in raw["questions"]), int(raw["defaults"])


@cache
def _pool_units() -> tuple[frozenset[str], ...]:
    return tuple(_units(q) for q in _pool()[0])


def suggestions(question: str) -> list[str]:
    """Up to :data:`HOW_MANY` questions to try instead, closest to ``question`` first; never ``question`` itself."""
    pool, defaults = _pool()
    asked = question.strip().lower()
    keep = [i for i, q in enumerate(pool) if q.lower() != asked]
    units = _pool_units()
    held: dict[str, int] = {}
    for i in keep:
        for unit in units[i]:
            held[unit] = held.get(unit, 0) + 1
    wanted = _units(question)

    def score(i: int) -> float:
        total = 0.0
        for unit in units[i]:
            if any(_same(unit, w) for w in wanted):
                total += (CONCEPT_WEIGHT if unit.startswith("c:") else 1.0) / held[unit]
        return total

    scores = {i: score(i) for i in keep}
    order = sorted(keep, key=lambda i: (-scores[i], i))
    picked = [pool[i] for i in order if scores[i] > 0][:HOW_MANY]
    for i in [*keep[:defaults], *keep]:
        if len(picked) == HOW_MANY:
            break
        if pool[i] not in picked:
            picked.append(pool[i])
    return picked
