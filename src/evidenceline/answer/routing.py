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
_HOW_DOES = r"how(?:\s+(?:exactly|actually))?(?:\s+(?:does|dose|do|would|will|can|did)|\s*'s)"
_ADVERB = r"(?:\s+(?:all|exactly|even|actually|really|just))?"
_GREETING = (
    r"(?:h+e+l+o+|hal+o+|h+i+|hiy+a+|he+y+a*|howdy|greetings|g'?day|good\s+(?:morning|afternoon|evening|day)|"
    r"morning|afternoon|evening|yo|sup|gm)(?:\s+(?:there|all|everyone|team|evidence\s*line|claude|bot|you|mate|"
    r"guys|folks|world))?"
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
    r"appreciate\s+it)(?:\s+(?:a\s+lot|so\s+much|very\s+much|heaps|again|kindly|for\s+(?:your|the)\s+help|"
    r"for\s+that|for\s+this|evidence\s*line|mate))*"
)
_ACK_WORD = (
    r"(?:ok(?:ay)?|great|cool|awesome|perfect|nice|good|brilliant|lovely|sure|alright|wow|interesting|neat|"
    r"impressive|understood|got\s+it|i\s+see|(?:ah|oh)\s+ok(?:ay)?|(?:that\s+)?makes\s+sense|love\s+it|well\s+done|"
    r"great\s+job|nice\s+work|this\s+is\s+(?:great|cool|neat|impressive)|that\s+helps|that'?s\s+helpful|"
    r"very\s+helpful|(?:good)?bye|see\s+(?:ya|you))"
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
            rf"{_WHAT_IS}\s+this\s+(?:web\s*)?site(?:\s+about)?",
            r"what\s+are\s+you(?:\s+(?:for|exactly|about|called))?",
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
            # examples
            r"(?:(?:can|could)\s+you\s+)?(?:show|give)\s+me\s+(?:an?\s+|some\s+)?examples?(?:\s+questions?)?",
            r"(?:any\s+)?examples?|(?:a\s+)?sample\s+questions?|suggest\s+(?:a\s+)?questions?|how\s+about\s+an\s+example",
            rf"{_WHAT_IS}\s+a\s+good\s+question(?:\s+to\s+ask)?",
            # who made it
            rf"who\s+(?:made|built|created|wrote|runs|owns|designed|developed|maintains|is\s+behind)\s+{_TOOL_STRICT}",
            r"who\s+is\s+the\s+(?:developer|author|creator|maker)|who\s+are\s+you",
            r"is\s+this\s+yours|is\s+this\s+your\s+(?:project|work|tool)",
            # help
            r"(?:can|could|will|would)\s+you\s+help(?:\s+me)?",
            rf"how\s+(?:can|could|do|does|will)\s+{_TOOL_STRICT}\s+help(?:\s+me)?",
            r"(?:i\s+need\s+(?:some\s+)?|can\s+i\s+get\s+(?:some\s+)?)?help(?:\s+me)?",
            # AI
            r"(?:are|is)\s+(?:you|this|it)\s+(?:an?\s+)?(?:real\s+)?(?:ai|a\.i|bot|robot|chat\s*bot|human|person|real|"
            r"claude|chat\s*gpt|gpt|llm)(?:[\s-]+powered|\s+or\s+(?:an?\s+)?(?:ai|bot|human|person|real\s+person))?",
            r"(?:does|do)\s+(?:this|it|you)\s+use\s+(?:ai|chat\s*gpt|gpt|claude|an?\s+llm)|"
            r"is\s+(?:this|it)\s+using\s+(?:ai|chat\s*gpt|gpt|claude)",
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
            r"first\s+time(?:\s+here)?|(?:just\s+)?(?:looking|browsing|checking\s+(?:this|it)\s+out)",
            r"huh|what|hmm+|eh",
            r"test(?:ing)?(?:\s+\d+)*",
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
_HAZARD_INDEX = re.compile(r"\W*HI\W*")
"""A bare 'HI' in capitals is the risk-assessment term hazard index, not a greeting."""


@dataclass(frozen=True)
class AboutQuestion:
    """A message that is only a greeting, thanks, or a question about Evidenceline itself."""

    kind: Literal["about", "thanks"]
    greeted: bool


def _plain(question: str) -> str:
    """Lower case, straight quotes, no emoji, symbols or emoticons: 'Hi' with a waving hand and ':)' reads 'hi'."""
    text = _EMOTICON.sub(" ", question.translate(_QUOTES).lower())
    return "".join(" " if unicodedata.category(ch) in {"So", "Sk", "Cf", "Mn"} else ch for ch in text)


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
    if len(question) > _MAX_ABOUT_CHARS or _HAZARD_INDEX.fullmatch(question):
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
