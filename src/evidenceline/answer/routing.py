"""Read a question for three things before any model is involved: is it about Evidenceline itself (a greeting,
thanks, or "how does this work?"), does it ask for a drinking-water value, and does it ask for a verdict (is the
site contaminated, is the water safe)?

Plain regular expressions, each testable on its own. The value route decides which verified guideline values go
into the prompt; the "about" and verdict routes answer with a fixed reply and never call a model.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from evidenceline.answer.numbers import read_numbers
from evidenceline.dataset import SUM_KEY
from evidenceline.guidance.scope import NAMED_ANALYTE, NUMERIC_QUESTION

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
_COMPARES = re.compile(
    r"\b(?:differen(?:ce|ces|t)|differ|compar(?:e|es|ed|ison)|chang(?:e|es|ed)|versus|vs|lower(?:ed)?|raised)\b",
    re.IGNORECASE,
)
_THE_UPDATE = re.compile(r"\b(?:2025|updates?|updated|ADWG|NHMRC)\b", re.IGNORECASE)
"""With :data:`_COMPARES`: a question about how the 2025 drinking-water values differ from PFAS NEMP 3.0 ('What did
the 2025 update change for PFOS?'), which needs both rules' values although it does not say 'value' or 'limit'.
'NEMP' alone is not enough: 'How has PFOS sampling changed in the new NEMP?' asks about sampling."""
_DRINKING_WATER = re.compile(r"\b(?:drinking[\s-]*water|potable|tap water|ADWG)\b", re.IGNORECASE)

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
_CLAUSE = re.compile(r"(?<=[.?!;,:])\s+")
"""Where a question splits into clauses: at punctuation followed by a space, so '0.05' stays whole. A verdict often
comes after the result it is about: 'My groundwater has 0.1 ug/L PFOA, does my site fail?'."""
_OWN = r"(?:my|our|this|that|their|your|his|her)"
_THING = (
    r"(?:site|sites|bore|bores|well|wells|water|groundwater|result|results|sample|samples|land|soil|property|block|"
    r"house|home|reading|readings|level|levels|tank|dam|pool)"
)
_OWN_THING = re.compile(rf"\b{_OWN}\s+(?:[\w-]+\s+){{0,2}}?{_THING}\b", re.IGNORECASE)
"""The visitor's own site, water or result: 'my site', 'our bore water', 'this PFOS result'."""
_NOT_BETWEEN = r"(?!(?:needs?|needed|has|have|had|must|required|detection|reporting|laboratory|lab)\b)"
"""Words that never come between the subject and the comparison of a verdict question: 'Does my site need to pass an
audit?' asks what the rules require, and 'our detection level' and 'the lab reporting limit' are the laboratory's,
not a result."""
_COMPARED = (
    r"(?:(?:fail(?:s|ed)?|pass(?:es|ed)?|exceed\w*)\b(?!\s+(?:the\s+|its\s+|their\s+)?holding\s+times?\b)|"
    rf"(?:over|above|under|below|within|beyond)\s+(?:the\s+|a\s+)?(?:{_NOT_BETWEEN}[\w.-]+\s+){{0,3}}?"
    r"(?:limits?|guidelines?|values?|levels?|criteri(?:on|a)|standards?|thresholds?)\b"
    r"(?!\s+of\s+(?:reporting|detection|quantitation)\b))"
    r"(?!(?:\s+[\w-]+){0,3}?\s+(?:reportable|notifiable|report\w*|notif\w*)\b)"
)
"""A result compared with a limit ('fail', 'exceed', 'over the drinking water limit'). Not a sample past its holding
time, a result below the laboratory's limit of reporting, or a comparison followed by a question about reporting it
('Is my result above the limit reportable to DWER?', 'Does a result above the limit need reporting?'): those ask how
the rules work."""
_YES_NO = r"(?:is|are|was|were|has|have|does|do|did|will|would)"
_CLAUSE_VERDICTS = (
    re.compile(
        rf"^{_YES_NO}\s+{_OWN}\s+(?:{_NOT_BETWEEN}[\w-]+\s+){{0,2}}?{_THING}\s+(?:{_NOT_BETWEEN}[\w-]+\s+){{0,2}}?"
        rf"{_COMPARED}",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:should|do|must)\s+(?:i|we|they)\s+stop\s+(?:drinking|swimming|eating|using\s+(?:the\s+|my\s+|our\s+)?"
        r"(?:bore\s+|tank\s+|tap\s+|well\s+|rain\s*|ground\s*)?water)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:am\s+i|are\s+we|are\s+(?:my|our)\s+(?:kids|children|family))\s+(?:safe|at\s+risk|in\s+danger|ok|okay|"
        r"going\s+to\s+be\s+(?:ok|okay|fine|sick))\b(?!\s+(?:to|for|with)\b)"
        r"(?!\s+of\s+(?:an?\s+)?(?:penalt(?:y|ies)|fines?|prosecution|breach\w*|offences?)\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:should|must)\s+(?:i|we)\s+(?:sell|buy|move\s+out\s+of|leave)\s+(?:my|our|the|this|a)\s+"
        r"(?:house|home|property|land|block)\b",
        re.IGNORECASE,
    ),
)
"""Verdict questions about the visitor's own situation, at the start of a clause: 'Does my site fail?', 'Is my result
over the limit?', 'Should I stop drinking my bore water?', 'Am I safe?', 'Should I sell my house because of PFAS?'.
'Am I OK to wear sunscreen when sampling?' asks about a method, so 'OK to' is left out here, and 'Are we at risk of a
penalty?' asks about the law."""
_WHEN_OWN = (
    re.compile(rf"^{_YES_NO}\s+(?:{_NOT_BETWEEN}[\w.+/-]+\s+){{0,5}}?{_COMPARED}", re.IGNORECASE),
    re.compile(
        r"^(?:should|do|must|need)\s+(?:i|we)\s+(?:need\s+to\s+|still\s+)?(?:be\s+)?(?:worr(?:y|ied)|concerned|"
        r"scared|afraid|alarmed)\b(?!\s+about\s+(?:the\s+|a\s+|any\s+)?(?:cross[\s-]*contamination|holding\s+times?|"
        r"which|how|what|when|whether|sampling|QA|QC|quality|paperwork|reporting|procedures?|methods?)\b)",
        re.IGNORECASE,
    ),
)
"""Verdict questions only when the question names the visitor's own site, water or result, or a concentration: 'Is
PFOS at 0.05 ug/L above the limit?', 'Should I be worried about PFAS in my tap water?'. Without one, 'Is a result
above the limit reported to DWER?' and 'Do we need to worry about cross contamination when sampling?' ask how the
rules work. The words before the comparison are few, so 'Do I need to report if our result is over the limit?' is
a question about reporting, not a verdict."""


def drinking_water_analytes(question: str) -> tuple[str, ...]:
    """The analytes whose drinking-water values the question asks for, in a fixed order; empty if none.

    The question must not name another medium such as soil or fresh water, and must either ask for a value (a
    limit, guideline value, criterion, level, concentration or number) and name PFOS, PFHxS, PFOA, PFBS or the PFOS
    and PFHxS sum, or ask how the 2025 drinking-water values changed ('What did the 2025 update change for PFOS?').
    A question about the change that names no analyte ('What is the difference between PFAS NEMP 3.0 and the 2025
    drinking water values?') gets every analyte with a drinking-water value, so both rules' tables are shown.
    """
    if _OTHER_MEDIUM.search(question):
        return ()
    about_the_update = bool(_COMPARES.search(question) and _THE_UPDATE.search(question))
    if not (NUMERIC_QUESTION.search(question) or about_the_update):
        return ()
    found: list[str] = []
    if _SUM.search(question):
        found.append(SUM_KEY)
    remaining = _SUM.sub(" ", question)
    found.extend(name for name in DRINKING_WATER_ANALYTES if _ANALYTE[name].search(remaining))
    if not found and about_the_update and _DRINKING_WATER.search(question):
        return DRINKING_WATER_ANALYTES
    return tuple(found)


def names_other_medium(question: str) -> bool:
    """Whether a value question names a medium other than drinking water (so no values are given)."""
    return bool(NUMERIC_QUESTION.search(question) and _OTHER_MEDIUM.search(question))


def asks_other_medium_value(question: str) -> bool:
    """Whether the question asks for one analyte's guideline value in a medium other than drinking water: 'What is
    the recreational water quality guideline value for PFOS?', 'What is the health investigation level for lead in
    soil?'. Only drinking-water values are verified, so such a value is never stated, and the model is not asked.
    A question that names no analyte ('What are health investigation levels for soil?') asks what the levels are,
    and goes to the model as usual."""
    return names_other_medium(question) and bool(NAMED_ANALYTE.search(question))


def mentions_water(question: str) -> bool:
    return bool(_WATER_WORD.search(question))


def _states_a_result(question: str) -> bool:
    return any(q.kind in {"water", "soil"} for q in read_numbers(question))


def _yes_no_verdict(text: str) -> bool:
    if not _YES_NO_START.search(text):
        return False
    if _PREDICATE_VERDICT.search(text):
        return True
    soft = _SOFT_VERDICT.search(text)
    return bool(soft and _ABOUT_SITE.search(text[: soft.start()]))


def asks_for_verdict(question: str) -> bool:
    """Whether the question asks Evidenceline to decide that a site is contaminated or water is safe.

    True for a yes/no question with a verdict word used as a predicate ('Is this site contaminated?', 'Are the
    results dangerous?', 'Is the bore water OK?', 'Is PFOS in my bore a problem?'), for 'does X mean the site is
    contaminated', for 'safe (or OK) to drink/use/swim' and for 'can we drink' or 'can my kids swim'. False for a
    question that only uses such a word as a label ('Are there rules for contaminated sites?') or asks how
    something is done ('When do I have to report a suspected contaminated site?'). Also true for a request to
    declare a verdict ('write that the results prove the site is contaminated'), whatever word it starts with.

    Each clause is also read on its own, so a verdict after the result it is about is caught: 'My bore has PFOS at
    0.1 ug/L, is that dangerous?', 'our bore tested 0.02 ug/L PFHxS, can my kids still swim in the pool'. So are
    questions about the visitor's own result ('Does my site fail?', 'Is PFOS at 0.05 ug/L above the limit?') and
    their own safety or decisions ('Am I safe?', 'Should I stop drinking my bore water?'); see
    :data:`_CLAUSE_VERDICTS` and :data:`_WHEN_OWN`.
    """
    if any(p.search(question) for p in (_SAFE_TO, _MEANS, _CAN_WE, _ASKED_TO_DECLARE)):
        return True
    if _yes_no_verdict(question):
        return True
    own = bool(_OWN_THING.search(question)) or _states_a_result(question)
    for clause in _CLAUSE.split(question.strip()):
        if _yes_no_verdict(clause) or _CAN_WE.search(clause) or any(p.search(clause) for p in _CLAUSE_VERDICTS):
            return True
        if own and any(p.search(clause) for p in _WHEN_OWN):
            return True
    return False


# --- Questions about Evidenceline itself ----------------------------------------------------------------------------

_MAX_ABOUT_CHARS = 200
"""A longer message is treated as a real question, whatever it starts with."""
_NOUN = (
    r"(?:thing|tool|app|box|question\s+box|service|demo|assistant|bot|chat\s*bot|chat|web\s*site|web\s*page|page|"
    r"system|ai|project|platform|search|software|program)"
)
_TOOL_STRICT = rf"(?:this|ths|thsi|it|evidence\s*line|you|this\s+{_NOUN}|the\s+{_NOUN})"
"""Evidenceline, the page or 'you'. Never a site: 'this site' or 'the site' could be a contaminated site."""
_TOOL = rf"(?:{_TOOL_STRICT}|this\s+site)"
"""Also 'this site', but only where it can only mean the website: 'what is this site?', 'how does this site work?'."""
_ME = r"(?:i|we)"
_WHAT_IS = r"(?:what|wat|wht|wot)(?:\s+(?:exactly|actually))?(?:\s+is|\s*'s|s)"
_HOW_DOES = r"(?:how|hw)(?:\s+(?:exactly|actually))?(?:\s+(?:does|dose|do|would|will|can|did)|\s*'s)"
_ADVERB = r"(?:\s+(?:all|exactly|even|actually|really|just))?"


def _written(*codes: int) -> str:
    """Letters given by their code points, so this file stays plain ASCII."""
    return "".join(map(chr, codes))


_POLITE = r"(?:\s+(?:krub|khrap|krap|ka|kha|kah))?"
"""The Thai polite endings 'khrap' and 'kha' in Latin letters ('sawasdee krub')."""
_THAI_POLITE = f"(?:{_written(0x0E04, 0x0E23, 0x0E1A)}|{_written(0x0E04, 0x0E30)})?"
"""The Thai polite endings 'khrap' and 'kha', as :func:`_plain` leaves them (without their vowel and tone marks)."""
_HELLO_ELSEWHERE = "|".join(
    [
        r"hola|bonjour|salut|hallo|guten\s+tag|ciao|namaste|konnichiwa|ni\s*hao|xin\s+chao|sawa?s?dee" + _POLITE,
        _written(0x0E2A, 0x0E27, 0x0E2A, 0x0E14) + _THAI_POLITE,  # Thai 'sawatdee'
        _written(0x4F60, 0x597D),  # Chinese 'ni hao'
        _written(0x60A8, 0x597D),  # Chinese 'nin hao'
        _written(0x3053, 0x3093, 0x306B, 0x3061, 0x306F),  # Japanese 'konnichiwa'
        _written(0xC548, 0xB155, 0xD558, 0xC138, 0xC694),  # Korean 'annyeonghaseyo'
        _written(0x0645, 0x0631, 0x062D, 0x0628, 0x0627),  # Arabic 'marhaba'
        _written(0x043F, 0x0440, 0x0438, 0x0432, 0x0435, 0x0442),  # Russian 'privet'
        "xin ch" + _written(0x00E0) + "o",  # Vietnamese 'xin chao', with its accent
    ]
)
"""Hello in other languages a visitor may try first. A question in another language still goes to the search."""
_THANKS_ELSEWHERE = "|".join(
    [
        r"(?:muchas\s+)?gracias|merci(?:\s+beaucoup)?|danke|grazie|obrigad[oa]|terima\s+kasih|arigato|xie\s*xie|"
        r"kob\s*kh?un" + _POLITE,
        _written(0x0E02, 0x0E2D, 0x0E1A, 0x0E04, 0x0E13) + _THAI_POLITE,  # Thai 'khop khun'
        _written(0x8C22, 0x8C22),  # Chinese 'xie xie'
        _written(0x3042, 0x308A, 0x304C, 0x3068, 0x3046),  # Japanese 'arigatou'
        _written(0xAC10, 0xC0AC, 0xD569, 0xB2C8, 0xB2E4),  # Korean 'kamsahamnida'
        _written(0x0634, 0x0643, 0x0631, 0x0627),  # Arabic 'shukran'
        _written(0x0441, 0x043F, 0x0430, 0x0441, 0x0438, 0x0431, 0x043E),  # Russian 'spasibo'
        "c" + _written(0x1EA3) + "m " + _written(0x01A1) + "n",  # Vietnamese 'cam on'
    ]
)
_GREETING = (
    r"(?:h+e+l+o+|hal+o+|h+i+|hiy+a+|he+y+a*|howdy|greetings|g'?day|good\s+(?:morning|afternoon|evening|day)|"
    rf"morning|afternoon|evening|yo|sup|gm|{_HELLO_ELSEWHERE})(?:\s+(?:there|all|everyone|team|evidence\s*line|"
    r"claude|bot|you|mate|guys|folks|world))?"
)
_GREETING_START = re.compile(rf"^{_GREETING}(?:\s+|$)")
_SMALL_TALK = re.compile(
    r"how\s+are\s+(?:you|things)(?:\s+doing)?(?:\s+today)?|how'?s\s+it\s+going|what'?s\s+up|whats\s+up|"
    r"how\s+do\s+you\s+do|nice\s+to\s+meet\s+you"
)
"""Counted as a greeting: 'Hi, how are you?'."""
_THANKS_WORD = (
    r"(?:(?:many\s+)?thanks|thank\s*(?:you|u)|thnks|thnk\s*(?:you|u)|tysm|thnx|thanx|thks|thx|ty|ta|cheers|"
    r"much\s+appreciated|"
    rf"appreciate\s+it|{_THANKS_ELSEWHERE})(?:\s+(?:a\s+lot|so\s+much|very\s+much|heaps|again|kindly|"
    r"for\s+(?:your|the)\s+help|"
    r"for\s+that|for\s+this|evidence\s*line|mate))*"
)
_ACK_WORD = (
    r"(?:ok(?:ay)?|great|cool|awesome|perfect|nice|good|brilliant|lovely|sure|alright|wow|interesting|neat|"
    r"impressive|understood|got\s+it|i\s+see|(?:ah|oh)\s+ok(?:ay)?|(?:that\s+)?makes\s+sense|love\s+it|well\s+done|"
    r"great\s+job|nice\s+work|this\s+is\s+(?:great|cool|neat|impressive)|that\s+helps|that'?s\s+helpful|"
    r"very\s+helpful|that\s+was\s+(?:very\s+|really\s+|so\s+)?(?:helpful|useful)|(?:good)?bye|see\s+(?:ya|you))"
)
_THANKS = re.compile(rf"(?:{_THANKS_WORD}|{_ACK_WORD})(?:\s+(?:{_THANKS_WORD}|{_ACK_WORD}))*")
"""Thanks, a goodbye, or a bare acknowledgement ('ok', 'great', 'got it thanks')."""
_ABOUT = re.compile(
    "|".join(
        [
            # how it works
            rf"{_HOW_DOES}\s+{_TOOL}{_ADVERB}\s+(?:work|works|wrk|wrok|werk|function|operate)(?:\s+(?:exactly|under\s+the\s+hood))?",
            rf"how\s+(?:is|was)\s+{_TOOL_STRICT}\s+(?:supposed\s+to\s+work|meant\s+to\s+work|used|built|made)",
            r"how\s+(?:did|do)\s+you\s+(?:build|make|create)\s+(?:this|it)",
            rf"how\s+(?:do|can|should|would)\s+{_ME}\s+(?:use|start|begin|get\s+started|ask(?:\s+a\s+question)?)"
            rf"(?:\s+(?:with\s+)?{_TOOL})?",
            rf"how\s+to\s+(?:use|start|ask)(?:\s+{_TOOL})?",
            # what it is and what it is for
            rf"{_WHAT_IS}\s+{_TOOL_STRICT}{_ADVERB}(?:\s+(?:for|about|used\s+for|called|named))?",
            rf"{_WHAT_IS}\s+(?:the\s+)?name\s+of\s+{_TOOL_STRICT}|what(?:\s+is|\s*'s|s)\s+(?:your|its)\s+name",
            r"what\s+do\s+(?:you|they|we|i|people)\s+call\s+(?:this|it|you)|"
            r"(?:does|do)\s+(?:this|it|you)\s+have\s+a\s+name",
            rf"{_WHAT_IS}\s+this\s+(?:web\s*)?site(?:\s+(?:about|for))?",
            r"(?:what|who)\s+(?:are|r)\s+(?:you|u)(?:\s+(?:for|exactly|about|called))?",
            r"(?:your\s+)?name",
            rf"{_WHAT_IS}\s+(?:the\s+)?(?:point|purpose|idea|deal)\s+(?:of|with)\s+{_TOOL_STRICT}",
            rf"{_WHAT_IS}\s+the\s+idea(?:\s+here)?",
            r"what'?s\s+going\s+on(?:\s+here)?|what'?s\s+in(?:\s+here)?",
            rf"what(?:\s+exactly)?(?:\s+(?:does|do|can|will)|\s*'s|s)\s+{_TOOL}\s+do(?:\s+for\s+(?:me|us))?",
            r"what\s+(?:am\s+i|are\s+we)\s+(?:looking\s+at|(?:supposed|meant)\s+to\s+(?:do|type|ask|write|put)"
            r"(?:\s+here)?)",
            rf"(?:what\s+(?:can|could|would)|why\s+(?:would|should))\s+{_ME}\s+use\s+{_TOOL_STRICT}(?:\s+for)?",
            rf"why\s+use\s+{_TOOL_STRICT}|who\s+is\s+{_TOOL_STRICT}\s+for",
            # what to ask
            rf"what\s+(?:can|do|should|could)\s+{_ME}\s+(?:ask|type|do|try|put|write|search)"
            rf"(?:\s+(?:you|it|this|evidence\s*line))?(?:\s+(?:here|about|in\s+the\s+box|in\s+here))?",
            rf"what\s+can\s+{_ME}\s+find(?:\s+here)?",
            r"what\s+(?:can|could|will)\s+(?:you|it|evidence\s*line)\s+(?:answer|help\s+(?:me\s+)?with|"
            r"tell\s+me(?:\s+about)?|do\s+for\s+me)",
            r"what\s+(?:do|does)\s+(?:you|it|evidence\s*line)\s+(?:know|cover)(?:\s+about)?",
            r"what\s+(?:topics|subjects)\s+(?:do|does)\s+(?:you|it|evidence\s*line)\s+cover",
            r"what\s+(?:(?:kinds?|types?|sorts?)\s+of\s+)?(?:questions|things|topics|stuff)\s+(?:can|do|should|could)\s+"
            r"(?:i|we|you)\s+(?:ask|answer|cover)(?:\s+(?:you|it|here|about))?",
            r"what\s+are\s+you\s+able\s+to\s+(?:answer|do)|what\s+are\s+your\s+capabilities",
            rf"can\s+{_ME}\s+ask(?:\s+you)?\s+anything",
            r"(?:i\s+have|i'?ve\s+got|(?:can|could|may)\s+i\s+ask(?:\s+you)?)\s+a\s+(?:quick\s+)?question",
            # examples
            r"(?:(?:can|could)\s+you\s+)?(?:show|give)\s+me\s+(?:an?\s+|some\s+)?examples?(?:\s+questions?)?",
            r"(?:any\s+)?examples?|(?:a\s+)?sample\s+questions?|suggest\s+(?:a\s+)?questions?|how\s+about\s+an\s+example",
            rf"{_WHAT_IS}\s+a\s+good\s+question(?:\s+to\s+ask)?",
            # who made it
            rf"who\s+(?:made|built|created|wrote|runs|owns|designed|developed|maintains|is\s+behind)\s+{_TOOL_STRICT}",
            r"who\s+is\s+the\s+(?:developer|author|creator|maker)",
            r"is\s+this\s+yours|is\s+this\s+your\s+(?:project|work|tool)",
            # help
            r"(?:can|could|will|would)\s+you\s+help(?:\s+me)?",
            rf"how\s+(?:can|could|do|does|will)\s+{_TOOL_STRICT}\s+help(?:\s+me)?",
            r"(?:i\s+need\s+(?:some\s+)?|can\s+i\s+get\s+(?:some\s+)?)?help(?:\s+me)?",
            r"(?:(?:can|could|will|would)\s+you\s+)?help\s+me\s+with\s+something",
            # AI
            r"(?:are|is)\s+(?:you|this|it)\s+(?:an?\s+)?(?:real\s+)?(?:ai|a\.i|bot|robot|chat\s*bot|human|person|real|"
            r"claude|chat\s*gpt|gpt|llm)(?:[\s-]+powered|\s+or\s+(?:an?\s+)?(?:ai|bot|human|person|real\s+person))?",
            r"(?:does|do)\s+(?:this|it|you)\s+use\s+(?:ai|chat\s*gpt|gpt|claude|an?\s+llm)|"
            r"is\s+(?:this|it)\s+(?:using|powered\s+by|run\s+by|built\s+(?:on|with))\s+(?:an?\s+)?"
            r"(?:ai|chat\s*gpt|gpt|claude|llm)",
            r"(?:which|what)\s+(?:model|ai|llm)(?:\s+(?:is\s+(?:this|it)|are\s+you|do\s+you\s+use|"
            r"does\s+(?:it|this|evidence\s*line)\s+use))?",
            r"am\s+i\s+(?:talking|speaking|chatting)\s+(?:to|with)\s+(?:an?\s+)?(?:bot|ai|robot|human|person|real\s+person)",
            # explain it
            rf"(?:tell\s+me\s+(?:more\s+)?about|explain|introduce|describe)\s+(?:yourself|{_TOOL_STRICT})",
            r"(?:(?:can|could)\s+you\s+(?:explain|tell\s+me|show\s+me)|explain)\s+(?:how\s+(?:this|it)\s+works|"
            r"what\s+(?:this|it)\s+(?:is|does)|how\s+to\s+use\s+(?:this|it))",
            r"explain|about|info|more\s+info|demo|is\s+(?:this|it)\s+a\s+demo",
            # is anyone there
            r"(?:is\s+(?:this|it)|are\s+you)\s+(?:working|on|live|online|there)",
            r"(?:is\s+)?(?:any\s*one|any\s*body)\s+(?:there|home)|(?:are\s+)?you\s+there|can\s+you\s+hear\s+me",
            rf"where\s+(?:do|should|can)\s+{_ME}\s+(?:start|begin|type|ask)",
            rf"can\s+{_ME}\s+try(?:\s+(?:it|this))?|let\s+me\s+try|let'?s\s+try",
            # trust and sources
            r"how\s+(?:accurate|reliable|good|trustworthy)\s+(?:is|are)\s+(?:this|it|you|the\s+answers)",
            r"(?:is|are)\s+(?:this|it|you|the\s+answers)\s+(?:accurate|reliable|trustworthy|correct)",
            rf"can\s+{_ME}\s+trust\s+(?:this|it|you|the\s+answers)",
            r"(?:does|do)\s+(?:this|it|you)\s+(?:make\s+mistakes|hallucinate|get\s+things\s+wrong)|"
            r"can\s+(?:this|it|you)\s+be\s+wrong",
            r"what\s+(?:documents|sources|guidelines|guidance|information|info|data|docs)\s+(?:do|does)\s+"
            r"(?:you|it|this|evidence\s*line)\s+(?:use|have|search|cover)",
            r"where\s+(?:does|do)\s+(?:the\s+|your\s+)?(?:info|information|data|answers?)\s+come\s+from",
            r"how\s+do\s+you\s+(?:get|find|make)\s+(?:the\s+|your\s+)?answers",
            # cost
            r"is\s+(?:this|it)\s+free|(?:does|do)\s+(?:this|it)\s+cost\s+(?:anything|money)|"
            r"how\s+much\s+(?:does\s+(?:this|it)\s+cost|is\s+(?:this|it))",
            # visitors introducing themselves, and a bare "what?"
            r"(?:i'?m|i\s+am)\s+(?:new(?:\s+here)?|just\s+(?:looking|browsing))",
            r"(?:i'?m|i\s+am)\s+(?:from|with|a|an)(?:\s+[\w'-]+){1,3}|(?:i'?m|i\s+am|my\s+name\s+is)\s+[a-z][\w'-]*"
            r"(?:\s+(?:from|with|at)(?:\s+[\w'-]+){1,3})?",
            r"first\s+time(?:\s+here)?|(?:just\s+)?(?:looking|browsing|checking\s+(?:this|it)\s+out)",
            r"huh|what|hmm+|eh",
            r"test(?:ing)?(?:\s+(?:test(?:ing)?|\d+))*",
        ]
    )
)
"""A question about Evidenceline itself, matched against a whole sentence, never part of one."""
_FILLER_WORDS = (
    r"(?:so|ok|okay|um+|uh+|hmm+|oh|well|right|and|but|then|just|sorry|excuse\s+me|please|pls|plz|btw|exactly|"
    r"(?:a\s+)?quick\s+question)"
)
_FILLER_START = re.compile(rf"^(?:{_FILLER_WORDS}\s+)+")
_FILLER_END = re.compile(r"(?:\s+(?:please|pls|plz|then|exactly|again|here|now|btw))+$")
_FILLER_ONLY = re.compile(_FILLER_WORDS)
_SENTENCE_BREAK = re.compile(r"[?.!,;:\n" + chr(0x2013) + chr(0x2014) + r"]+|\s+-\s+")
_EMOTICON = re.compile(r"(?<!\w)(?:[:;=8][-'^]?[)(\]\[dpo3/\\|*]+|<3+|x-?d+)(?!\w)")
_QUOTES = str.maketrans({chr(0x2018): "'", chr(0x2019): "'", '"': " ", chr(0x201C): " ", chr(0x201D): " "})


@dataclass(frozen=True)
class AboutQuestion:
    """A message that is only a greeting, thanks, or a question about Evidenceline itself."""

    kind: Literal["about", "thanks"]
    greeted: bool


def _plain(question: str) -> str:
    """Lower case, straight quotes, no emoji, symbols or emoticons: 'Hi' with a waving hand and ':)' reads 'hi'.
    Full-width letters read as plain ones (NFKC), and combining marks are dropped, so the Thai 'sawatdee' keeps its
    letters in one word."""
    text = _EMOTICON.sub(" ", unicodedata.normalize("NFKC", question).translate(_QUOTES).lower())
    return "".join(
        "" if unicodedata.category(ch) == "Mn" else " " if unicodedata.category(ch) in {"So", "Sk", "Cf"} else ch
        for ch in text
    )


def _strip_start(segment: str) -> tuple[str, bool]:
    """The segment without leading fillers and greetings ('um hello, ...'), and whether it held a greeting."""
    greeted = False
    while True:
        filler = _FILLER_START.match(segment)
        greeting = _GREETING_START.match(segment)
        if filler:
            segment = segment[filler.end() :]
        elif greeting:
            greeted = True
            segment = segment[greeting.end() :]
        else:
            return segment, greeted


def about_evidenceline(question: str) -> AboutQuestion | None:
    """Whether the whole message is a greeting, thanks, or a question about Evidenceline itself; None otherwise.

    'Hello, how does this work?', 'What can I ask?', 'Are you an AI?', 'Can I trust this?', 'Thanks!', 'hii' and a
    bare emoji qualify. Every sentence of the message must be one of those, so 'How does PFAS move in groundwater?',
    'Hi, what is the PFOS limit?', 'Can you help me find the PFOS limit?' and 'Who owns this site?' do not: they go
    to the guidance search as usual.
    """
    if len(question) > _MAX_ABOUT_CHARS:
        return None
    if question.strip() and not any(ch.isalnum() for ch in question):
        return AboutQuestion("about", greeted=False)  # only emoji or punctuation, such as a waving hand or '??'
    greeted = asked = thanked = filler = False
    for part in _SENTENCE_BREAK.split(_plain(question)):
        segment, hello = _strip_start(" ".join(part.split()))
        greeted = greeted or hello
        segment = _FILLER_END.sub("", segment).strip()
        if segment == "":
            continue
        if _SMALL_TALK.fullmatch(segment):
            greeted = True
        elif _ABOUT.fullmatch(segment):
            asked = True
        elif _THANKS.fullmatch(segment):
            thanked = True
        elif _FILLER_ONLY.fullmatch(segment):
            filler = True
        else:
            return None
    if asked or (greeted and not thanked) or (filler and not thanked):
        return AboutQuestion("about", greeted)
    if thanked:
        return AboutQuestion("thanks", greeted)
    return None
