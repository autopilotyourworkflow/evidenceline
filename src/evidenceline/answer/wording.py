"""Wording an answer must never use, as plain regular expressions: a verdict (water is safe, a site is contaminated),
picking one rule over the other, and dashes. No model is involved.

A verdict phrase is not counted when it is the thing being asked about rather than said: 'if the site is
contaminated', 'know or suspect it is contaminated', 'whether the water is safe'. The excusing word must be in the
same clause and within four words before the phrase. Everything else errs towards withholding the answer.
"""

from __future__ import annotations

import re

DASHES: tuple[str, ...] = ("\u2012", "\u2013", "\u2014", "\u2015", "\u2e3a", "\u2e3b", "\ufe31", "\ufe32", "\ufe58")
"""Figure dash, en dash, em dash, horizontal bar, two- and three-em dashes, and their presentation forms."""

_COPULA = (
    r"(?:is|are|was|were|be|being|been|remains?|stays?|looks?|seems?|appears?(?:\s+to\s+be)?|"
    r"isn['\u2019]?t|aren['\u2019]?t|wasn['\u2019]?t|weren['\u2019]?t|(?<=\w)['\u2019]s)"
)
_ADVERB = (
    r"(?:not|no\s+longer|therefore|thus|hence|so|still|now|also|clearly|definitely|probably|likely|certainly|"
    r"perfectly|completely|entirely|fully|quite|very|totally|actually|indeed|then|generally|considered|deemed|"
    r"regarded\s+as|found\s+to\s+be|shown\s+to\s+be|confirmed\s+(?:as|to\s+be)|technically|legally|officially|"
    r"effectively|essentially|basically|largely|mostly|reasonably|relatively|sufficiently|acceptably)"
)
_STATE = (
    r"(?:safe|unsafe|contaminated|uncontaminated|polluted|unpolluted|clean|harmless|dangerous|hazardous|toxic|"
    r"poisonous|drinkable|undrinkable|potable|non-potable|fine|ok|okay|alright|all\s+right|unfit|risky)"
)
_NOT_A_VERDICT_NOUN = r"(?!\s+(?:fill|waste|materials?|substances?|chemicals?|compounds?|goods|up|air)\b)"
"""'is clean fill' or 'is hazardous waste' name a category, not a verdict."""
_USE = (
    r"(?:drink|drinking|use|using|swim|swimming|bathe|bathing|eat|eating|consume|consumption|irrigate|irrigation|"
    r"water\s+(?:the\s+)?garden|play|live|grow)"
)

VERDICT = re.compile(
    rf"\b{_COPULA}\s+(?:{_ADVERB}\s+){{0,3}}{_STATE}\b(?!-){_NOT_A_VERDICT_NOUN}|"
    rf"\b(?:considered|deemed|regarded\s+as|judged|thought\s+to\s+be|believed\s+to\s+be)\s+(?:{_ADVERB}\s+){{0,2}}"
    rf"(?:safe|unsafe|harmless)\b(?!-){_NOT_A_VERDICT_NOUN}|"
    r"\b(?:unlikely|not\s+(?:expected|likely))\s+to\s+(?:harm|hurt|endanger|cause\s+(?:any\s+)?harm)\b|"
    r"\b(?:will|would|does|do|did|can|could)\s*(?:not|n['\u2019]t)\s+(?:harm|hurt|endanger)\b|"
    rf"\b(?:safe|fine|ok|okay|alright|fit|unfit)\s+(?:to|for)\s+(?:human\s+)?{_USE}\b|"
    r"\b(?:suitable|unsuitable|acceptable|unacceptable|good)\s+(?:to|for)\s+(?:human\s+)?(?:drink|drinking|swim|"
    r"swimming|bathe|bathing|consumption)\b|"
    r"\b(?:poses?|posed|posing|presents?|presented|carr(?:y|ies|ied))\s+(?:no|little|negligible|minimal|zero|"
    r"a\s+(?:serious|significant|real|high|low|health)|an?\s+unacceptable|serious|significant|any)\s+"
    r"(?:\w+\s+){0,2}risks?\b|"
    r"\b(?:does|do|did|will|would)\s*(?:not|n['\u2019]t)\s+pose\s+(?:a|any)\s+(?:\w+\s+){0,2}risks?\b|"
    r"\bno\s+(?:\w+\s+)?(?:health\s+)?(?:risk|concern|cause\s+for\s+concern|danger)s?\s+(?:to|for|from)\b|"
    r"\b(?:nothing|no\s+need)\s+to\s+worry\b|"
    r"\bsafe(?:\s+|-)(?:level|limit|concentration|amount|threshold|dose|value|number)s?\b|"
    r"\b(?:results?|samples?|site|water|groundwater|well|bore|land|soil)\s+(?:fails?|failed)\b|"
    r"\b(?:fails?|failed|passes|passed)\s+(?:the\s+)?(?:guideline|limit|criteria|criterion|standard|test|screening)",
    re.IGNORECASE,
)
"""Saying that water is safe or unsafe, that a site is or is not contaminated, or that a result passes or fails.
Plain-word forms count too: 'the level considered safe', 'unlikely to harm health', 'will not harm you'."""

_EXCUSE = re.compile(
    r"\b(?:if|whether|unless|once|until|where|when|suspects?|suspected)\b",
    re.IGNORECASE,
)
_CLAUSE_BREAK = re.compile(r"[,;:()]")

RULE_PICK = re.compile(
    r"\b(?:should|must|need\s+to|needs\s+to|ought\s+to|has\s+to|have\s+to|recommended\s+to|best\s+to)\s+"
    r"(?:be\s+)?(?:use[ds]?|appl(?:y|ied)|adopt(?:ed)?|follow(?:ed)?|rel(?:y|ied)\s+on|go\s+with|choose|chosen)\b|"
    r"\bthe\s+(?:applicable|correct|right|relevant|appropriate|proper|valid|binding|operative|governing|"
    r"preferred|recommended)\s+(?:rule|value|guideline|limit|standard|one|criterion|criteria|set)s?\b|"
    r"\b(?:applies|apply)\s+in\s+(?:WA|Western\s+Australia)\b|"
    r"\bthe\s+(?:one|ones|rule|value|guideline|standard)s?\s+to\s+(?:use|rely\s+on|follow|apply|go\s+with)\b|"
    r"\brel(?:y|ies|ied)\s+on\b|\bgo\s+with\b|"
    r"\btakes?\s+precedence\b|\btook\s+precedence\b|\bprecedence\s+over\b|\bprevails?\b|\bprevailed\b|"
    r"\bsupersed(?:e|es|ed|ing)\b|\boverrid(?:e|es|den|ing)\b|\breplaced\s+by\b|\breplaces\b|"
    r"\bin\s+force\b|\bpreferred\b|\boutdated\b|\bobsolete\b|\bout\s+of\s+date\b|"
    r"\bno\s+longer\s+(?:applies|apply|used|in\s+use|valid|current|relevant)\b",
    re.IGNORECASE,
)
"""Saying which rule applies, is correct, or should be used; or that one replaces the other."""


def _excused(text: str, start: int) -> bool:
    """Whether a verdict phrase starting at ``start`` is a condition or a question, not a statement."""
    before = text[:start]
    clause = _CLAUSE_BREAK.split(re.split(r"[.!?]\s", before)[-1])[-1]
    words = clause.split()[-4:]
    return bool(_EXCUSE.search(" ".join(words)))


def verdicts(text: str) -> list[str]:
    """Each verdict phrase in ``text``, as written, leaving out ones that are a condition ('if it is safe')."""
    return [m.group(0) for m in VERDICT.finditer(text) if not _excused(text, m.start())]


def rule_picks(text: str) -> list[str]:
    """Each phrase in ``text`` that picks one rule over the other."""
    return [m.group(0) for m in RULE_PICK.finditer(text)]


def dashes(text: str) -> list[str]:
    """The dash characters in ``text``, written as U+XXXX."""
    return [f"U+{ord(c):04X}" for c in DASHES if c in text]
