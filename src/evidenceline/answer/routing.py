"""Read a question for two things before any model is involved: does it ask for a drinking-water value, and does
it ask for a verdict (is the site contaminated, is the water safe)?

Plain regular expressions, each testable on its own. The value route decides which verified guideline values go
into the prompt; the verdict route answers with a fixed reply and never calls a model.
"""

from __future__ import annotations

import re

from evidenceline.dataset import SUM_KEY
from evidenceline.guidance.scope import NUMERIC_QUESTION

DRINKING_WATER_ANALYTES: tuple[str, ...] = ("PFOS", "PFHxS", "PFOA", "PFBS")
"""Analytes with a drinking-water value in ``guidelines.json`` under at least one rule."""

_SUM = re.compile(r"\bPFOS\s*(?:\+|and|&|plus)\s*PFHxS\b|\bPFHxS\s*(?:\+|and|&|plus)\s*PFOS\b", re.IGNORECASE)
_ANALYTE = {name: re.compile(rf"\b{name}\b", re.IGNORECASE) for name in DRINKING_WATER_ANALYTES}
_OTHER_MEDIUM = re.compile(
    r"\b(?:soils?|sediments?|fresh\s*water|freshwater|marine|ecolog\w*|recreation\w*|biota|fish|livestock|"
    r"irrigation|air|vapou?r|landfill|leachate|compost|biosolids?)\b",
    re.IGNORECASE,
)
"""A medium other than drinking water. Only drinking-water values are loaded, so such a question gets none."""
_WATER_WORD = re.compile(r"\b(?:drinking[\s-]*water|potable|tap water|ADWG|water)\b", re.IGNORECASE)

_VERDICT_WORD = (
    r"(?:contaminated|polluted|safe|unsafe|dangerous|harmful|toxic|hazardous|drinkable|potable|fit to drink|clean)"
)
_ATTRIBUTIVE_NOUN = (
    r"(?:sites?|land|soils?|water|groundwater|materials?|sediments?|areas?|propert(?:y|ies)|ground|fill|premises)"
)
_PREDICATE_VERDICT = re.compile(rf"\b{_VERDICT_WORD}\b(?!\s+{_ATTRIBUTIVE_NOUN}\b)", re.IGNORECASE)
"""A verdict word used as a predicate ('is this site contaminated?'), not as a label ('contaminated sites')."""
_YES_NO_START = re.compile(
    r"^\s*(?:so,?\s+|and\s+|but\s+|ok,?\s+)?(?:is|are|was|were|does|do|did|would|will|could|can|should|has|have)\b",
    re.IGNORECASE,
)
_MEANS = re.compile(rf"\bmeans?\b(?:\s+that)?[^?.!]{{0,60}}?\b{_VERDICT_WORD}\b", re.IGNORECASE)
_ASKED_TO_DECLARE = re.compile(
    rf"\b(?:prove|confirm|state|write|say|declare|certify|conclude)s?\s+(?:in\s+writing\s+|formally\s+|"
    rf"officially\s+)?that\b[^?.!]{{0,80}}?\b(?:is|are|was|were)\s+(?:not\s+)?{_VERDICT_WORD}\b"
    rf"(?!\s+{_ATTRIBUTIVE_NOUN}\b)",
    re.IGNORECASE,
)
"""A request to declare a verdict rather than a question about one: 'write that the results prove the site is
contaminated', 'confirm in writing that the water is safe'. Each verb counts only with 'that' after it, so 'What does
the guidance say is required when a site is contaminated?' and 'What evidence would prove a site is contaminated?'
are not caught."""
_USE = r"(?:drink|drinking|use|using|swim|swimming|eat|eating|live|garden|grow|touch|irrigate|bathe|play|consume)"
_SAFE_TO = re.compile(
    rf"\b(?:safe|ok|okay|alright|all\s+right|fine|fit|good|suitable)\s+(?:to|for)\s+(?:human\s+)?{_USE}\b",
    re.IGNORECASE,
)
"""'safe to drink', 'OK to drink', 'fine for swimming'."""
_CAN_WE = re.compile(
    r"^\s*(?:so,?\s+|and\s+|but\s+|ok,?\s+)?(?:can|could|should|may|might|would|will|is\s+it\s+ok\s+to|"
    r"is\s+it\s+okay\s+to)\s+(?:i|we|you|they|people|someone|anyone|one|kids|children|residents|"
    r"(?:my|our|the|their|your)\s+\w+)\s+(?:still\s+|safely\s+)?(?:drink|swim|bathe|eat|irrigate|garden|"
    r"grow|play|water\s+(?:the\s+)?garden|use\s+(?:the\s+)?(?:bore|water|well))\b",
    re.IGNORECASE,
)
"""'Can my kids swim in it', 'Could we drink from the bore?': asking whether something is safe, without the word."""
_SOFT_VERDICT = re.compile(
    r"\b(?:(?:ok|okay|alright|all\s+right|fine)\b(?!\s+(?:to|for)\b)|a\s+(?:problem|concern|worry|risk|danger|"
    r"health\s+risk)\b|an\s+(?:issue|problem)\b|risky\b|poisonous\b|bad\s+for\b)",
    re.IGNORECASE,
)
"""Softer verdict words, counted only in a yes/no question whose subject is the site, the water or a result (named
before the word): 'Is the bore water OK?', not 'Is it OK to composite samples?' or 'Is there a problem with PFAS
sampling?'."""
_ABOUT_SITE = re.compile(
    r"\b(?:water|bore|bores|well|wells|groundwater|site|land|soil|property|results?|levels?|readings?|samples?|"
    r"concentrations?|PFAS|PFOS|PFHxS|PFOA|PFBS|it)\b",
    re.IGNORECASE,
)


def drinking_water_analytes(question: str) -> tuple[str, ...]:
    """The analytes whose drinking-water values the question asks for, in a fixed order; empty if none.

    The question must ask for a value (a limit, guideline value, criterion, level or concentration), name PFOS,
    PFHxS, PFOA, PFBS or the PFOS and PFHxS sum, and not name another medium such as soil or fresh water.
    """
    if not NUMERIC_QUESTION.search(question) or _OTHER_MEDIUM.search(question):
        return ()
    found: list[str] = []
    if _SUM.search(question):
        found.append(SUM_KEY)
    remaining = _SUM.sub(" ", question)
    found.extend(name for name in DRINKING_WATER_ANALYTES if _ANALYTE[name].search(remaining))
    return tuple(found)


def names_other_medium(question: str) -> bool:
    """Whether a value question names a medium other than drinking water (so no values are given)."""
    return bool(NUMERIC_QUESTION.search(question) and _OTHER_MEDIUM.search(question))


def mentions_water(question: str) -> bool:
    return bool(_WATER_WORD.search(question))


def asks_for_verdict(question: str) -> bool:
    """Whether the question asks Evidenceline to decide that a site is contaminated or water is safe.

    True for a yes/no question with a verdict word used as a predicate ('Is this site contaminated?', 'Are the
    results dangerous?', 'Is the bore water OK?', 'Is PFOS in my bore a problem?'), for 'does X mean the site is
    contaminated', for 'safe (or OK) to drink/use/swim' and for 'can we drink' or 'can my kids swim'. False for a
    question that only uses such a word as a label ('Are there rules for contaminated sites?') or asks how
    something is done ('When do I have to report a suspected contaminated site?'). Also true for a request to
    declare a verdict ('write that the results prove the site is contaminated'), whatever word it starts with.
    """
    if any(p.search(question) for p in (_SAFE_TO, _MEANS, _CAN_WE, _ASKED_TO_DECLARE)):
        return True
    if not _YES_NO_START.search(question):
        return False
    if _PREDICATE_VERDICT.search(question):
        return True
    soft = _SOFT_VERDICT.search(question)
    return bool(soft and _ABOUT_SITE.search(question[: soft.start()]))
