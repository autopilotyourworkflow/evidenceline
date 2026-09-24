"""Propose a spelling correction for a question that found nothing: 'waht is the pfos limt' becomes 'what is the pfos
limit'.

A proposal only. The question box offers it as "Did you mean ...?" and asks it only if the visitor chooses it
(:mod:`evidenceline.answer.pipeline`); a question is never rewritten behind the visitor's back, because without a
dictionary a real word the guidance does not use ('chlorate', 'eighty', 'Bunnings') cannot be told from a typo.

A word is changed only when all of these hold:

- no indexed passage contains it, it has at least :data:`MIN_LENGTH` letters, and it is written in lower case (or with
  a first capital at the start of a sentence), so a name ('Kwinana', 'Leda', 'Bunnings') or an acronym is left alone;
- it is not a number word ('eighty'), a contraction typed without its apostrophe ('cant', 'dont': changing them flips
  the meaning), a PFAS-family abbreviation ('pfhxsa') or a chemical name ('chlorate', 'tetrachloroethane'), because a
  near neighbour of one of those is a different thing;
- a word the guidance uses at least :data:`MIN_USES` times starts with the same letter and is within one edit (two
  for a word of :data:`LONG_WORD` letters or more), counting two swapped neighbouring letters as one edit;
- that word is clearly the best: the closest, then the same letters in another order ('laed' is 'lead', not
  'land'), and no other word as close is nearly as common ('limt' is 'limit', used about twice as often as 'list');
- it is not a common word ('plus', 'want', 'done') unless the typed word holds the same letters in another order
  ('waht'), and it is not already in the question.

The question must also show it is on the guidance's subject: a word the guidance uses, as typed ('pfos' in 'waht is
the pfos limt'), or a correction that only swapped two letters ('teir' to 'tier'). A question made only of words the
guidance never uses ('How do I bake sourdough bread?') is off topic, not misspelt.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import cache

MIN_LENGTH = 4
LONG_WORD = 7
MIN_USES = 2
CLEAR_WINNER = 1.5
"""The best word must be used at least this many times as often as any other word just as close."""

_TOKEN = re.compile(r"\[[^\]]*\]|[A-Za-z0-9][A-Za-z0-9']*")
_CHEMICAL = re.compile(
    r"(?:chlor|fluor|brom|iod|meth|eth|prop|benz|phen|tolu|xyl|sulf|sulph|nitr|phosph|cyan|ars|carb|hydr|oxy|amin|"
    r"amid|chrom|cadm|merc|mangan|tetra|tri|hex|pent|hept|oct|non|dec)[a-z]*(?:ate|ite|ide|ane|ene|yne|ine|ol|one|"
    r"ium|yl|ic)$"
)
"""A chemical name: a chemical root and ending ('chlorate', 'tetrachloroethane', 'arsenite'). Its near neighbour
('chloride', 'tetrachloroethene', 'arsenic') is a different chemical, so neither is ever corrected to the other."""
_NUMBER_WORDS = frozenset(
    [
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "thousand",
        "million",
        "billion",
        "half",
        "quarter",
        "first",
        "second",
        "third",
    ]
)
_CONTRACTIONS = frozenset(
    [
        "cant",
        "dont",
        "wont",
        "isnt",
        "arent",
        "doesnt",
        "didnt",
        "hasnt",
        "havent",
        "hadnt",
        "wasnt",
        "werent",
        "shouldnt",
        "couldnt",
        "wouldnt",
        "mustnt",
        "neednt",
        "aint",
        "youre",
        "theyre",
        "thats",
        "whats",
        "heres",
        "theres",
        "wheres",
        "whos",
        "hows",
        "youve",
        "theyve",
        "youll",
        "theyll",
        "shes",
        "itll",
    ]
)
_COMMON = frozenset(
    [
        "what",
        "when",
        "where",
        "which",
        "while",
        "whom",
        "whose",
        "that",
        "this",
        "these",
        "those",
        "there",
        "their",
        "them",
        "they",
        "then",
        "than",
        "with",
        "from",
        "into",
        "onto",
        "have",
        "been",
        "being",
        "does",
        "done",
        "doing",
        "should",
        "would",
        "could",
        "must",
        "will",
        "shall",
        "also",
        "plus",
        "want",
        "wants",
        "made",
        "make",
        "many",
        "much",
        "more",
        "most",
        "some",
        "such",
        "only",
        "over",
        "very",
        "well",
        "were",
        "your",
        "yours",
        "about",
        "above",
        "after",
        "again",
        "against",
        "before",
        "below",
        "between",
        "both",
        "each",
        "other",
        "same",
        "under",
        "until",
        "upon",
        "change",
        "need",
        "needs",
        "know",
        "tell",
        "help",
        "find",
        "like",
        "work",
        "good",
        "best",
        "used",
        "using",
        "give",
        "take",
        "look",
        "come",
        "going",
        "thing",
        "things",
        "people",
        "time",
        "year",
        "years",
        "place",
        "part",
        "case",
        "point",
        "number",
        "show",
        "still",
        "just",
        "even",
        "back",
        "first",
        "last",
        "long",
        "great",
        "little",
        "right",
        "high",
        "every",
        "never",
        "always",
        "here",
    ]
)
"""Everyday words: never a correction's target unless the letters only swapped ('waht' to 'what'), and never the
word that shows a question is on the guidance's subject."""
FUNCTION_WORD_TYPOS = {
    "wht": "what",
    "wat": "what",
    "whta": "what",
    "wot": "what",
    "shuld": "should",
    "shoud": "should",
    "shld": "should",
    "teh": "the",
    "hte": "the",
    "whn": "when",
    "wen": "when",
    "hw": "how",
    "hwo": "how",
    "adn": "and",
}
"""Common slips in short everyday words, which the rules above never change (too short, or a common word): without
them 'wht shuld a DSI reprt include' was offered as 'wht shuld a DSI report include'. Changed only when no indexed
passage uses the word as typed, and like every other change, only in a question on the guidance's subject."""
_WORD = re.compile(r"[a-z]+")


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


def _allowed_target(typed: str, known: str, question_words: frozenset[str]) -> bool:
    if known in question_words or known in _NUMBER_WORDS or known.startswith("pf") or _CHEMICAL.search(known):
        return False
    return known not in _COMMON or sorted(known) == sorted(typed)


def _closest(typed: str, candidates: tuple[tuple[str, int], ...], question_words: frozenset[str]) -> str | None:
    """The one clearly best word for ``typed``, or None when there is none or two are about as likely."""
    limit = 2 if len(typed) >= LONG_WORD else 1
    # Closest first; among equally close words, the same letters in another order first ('laed' is 'lead', not
    # 'land'): swapped letters are the commonest slip. Then the more used word.
    scored = sorted(
        (distance, sorted(known) != sorted(typed), -uses, known)
        for known, uses in candidates
        if (distance := _edit_distance(typed, known, limit)) <= limit
    )
    if not scored:
        return None
    distance, reordered, uses, known = scored[0]
    rivals = [u for d, r, u, _ in scored[1:] if (d, r) == (distance, reordered)]
    if rivals and -rivals[0] * CLEAR_WINNER > -uses:
        return None
    return known if _allowed_target(typed, known, question_words) else None


def _may_change(token: str, start: int, question: str, words: Counter[str]) -> bool:
    lower = token.lower()
    before = question[:start].rstrip()
    at_sentence_start = before == "" or before[-1] in ".?!"
    return (
        token.isalpha()
        and len(token) >= MIN_LENGTH
        and lower not in words
        and (token == lower or (token == lower.capitalize() and at_sentence_start))
        and lower not in _NUMBER_WORDS
        and lower not in _CONTRACTIONS
        and not lower.startswith("pf")
        and not _CHEMICAL.search(lower)
    )


def corrected(question: str, words: Counter[str]) -> str | None:
    """A proposed spelling of the question, with each misspelt word replaced by the one word the guidance uses that
    it clearly meant. None when no word changes, or when nothing shows the question is on the guidance's subject: no
    word the guidance uses as typed, and no correction that only swapped two letters ('teir' to 'tier')."""
    groups = _by_first_letter(tuple(sorted(words.items())))
    question_words = frozenset(_WORD.findall(question.lower()))
    changed = False
    anchored = False
    pieces: list[str] = []
    last = 0
    for match in _TOKEN.finditer(question):
        token = match.group(0)
        replacement = token
        lower = token.lower()
        if lower in FUNCTION_WORD_TYPOS and lower not in words:
            replacement = FUNCTION_WORD_TYPOS[lower].capitalize() if token[0].isupper() else FUNCTION_WORD_TYPOS[lower]
            changed = True
        elif _may_change(token, match.start(), question, words):
            found = _closest(lower, groups.get(lower[0], ()), question_words)
            if found is not None:
                replacement = found.capitalize() if token[0].isupper() else found
                changed = True
                # Swapped letters ('teir' for 'tier') are a sure sign of a slip, and show the question is on topic.
                anchored = anchored or (sorted(found) == sorted(lower) and found not in _COMMON)
        elif token.isalpha() and len(token) >= MIN_LENGTH and lower in words and lower not in _COMMON:
            anchored = True
        pieces.append(question[last : match.start()] + replacement)
        last = match.end()
    pieces.append(question[last:])
    return "".join(pieces) if changed and anchored else None
