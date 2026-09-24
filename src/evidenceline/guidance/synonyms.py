"""Turn a question into search concepts, expanding domain terms that the guidance writes in more than one way.

A *concept* is one idea in the question: either a synonym group from :data:`SYNONYM_GROUPS` ("drinking water" also
finds "potable") or a single remaining word. Each concept becomes one FTS5 expression, and the no-match check weighs
how much of the question a passage covers, concept by concept, so a group counts once however many spellings it has.

Each group has two lists:

- ``phrases`` are searched in the index. Every phrase is written the way at least one indexed document writes the
  idea; the comment on each group names one place.
- ``triggers`` are extra ways a *question* may say it that are not searched themselves, usually because the index's
  stemmer would make them match unrelated text. The porter stemmer turns "container", "containment" and "contains"
  into the same stem, so a question about sample *containers* is searched as "sample container", "bottle", "HDPE"
  and "polypropylene", never as the bare word.

Phrases and triggers are matched on the question's words in order, first with every word (so "limit of reporting"
matches), then with common words left out (so "PFAS samples be stored" finds the trigger "samples stored").
The map is written by hand and is deliberately not a thesaurus. Add a group only with an example from a document.

:func:`question_phrases` gives the pairs of neighbouring words the question uses ("site assessment", "soil
samples"); a passage that has the same pair side by side is ranked higher (see :mod:`evidenceline.guidance.ranking`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SynonymGroup:
    """One idea, the phrases searched for it, and extra question wordings that map to it."""

    label: str
    phrases: tuple[str, ...]
    triggers: tuple[str, ...] = ()


SYNONYM_GROUPS: tuple[SynonymGroup, ...] = (
    SynonymGroup("drinking water", ("drinking water", "potable", "potable water", "drinking-water")),
    SynonymGroup(
        "guideline value",
        (
            "investigation level",
            "assessment level",
            "guideline value",
            "screening level",
            "screening value",
            "screening criteria",
            "criterion",
            "criteria",
        ),
    ),
    SynonymGroup(
        "PFOS", ("pfos", "perfluorooctane sulfonate", "perfluorooctane sulfonic acid", "perfluorooctane sulphonate")
    ),
    SynonymGroup("PFOA", ("pfoa", "perfluorooctanoic acid", "perfluorooctanoate")),
    SynonymGroup(
        "PFHxS", ("pfhxs", "perfluorohexane sulfonate", "perfluorohexane sulfonic acid", "perfluorohexane sulphonate")
    ),
    SynonymGroup("PFBS", ("pfbs", "perfluorobutane sulfonate", "perfluorobutane sulfonic acid")),
    SynonymGroup("PFAS", ("pfas", "per and poly fluoroalkyl substances", "per and polyfluoroalkyl substances")),
    SynonymGroup("groundwater", ("groundwater", "ground water", "aquifer")),
    SynonymGroup(
        "limit of reporting",
        ("limit of reporting", "lor", "limit of detection", "detection limit", "practical quantitation limit", "pql"),
    ),
    SynonymGroup("detailed site investigation", ("detailed site investigation", "dsi")),
    SynonymGroup("preliminary site investigation", ("preliminary site investigation", "psi")),
    SynonymGroup("sampling and analysis quality plan", ("sampling and analysis quality plan", "saqp")),
    # ASC NEPM Schedule B1 (PDF p. 11) and PFAS NEMP 3.0 (PDF p. 33): "conceptual site model (CSM)".
    SynonymGroup("conceptual site model", ("conceptual site model", "csm")),
    SynonymGroup("DWER", ("dwer", "department of water and environmental regulation")),
    SynonymGroup("health investigation level", ("health investigation level", "hil")),
    SynonymGroup("health screening level", ("health screening level", "hsl")),
    # NEPM B1 section 2.5 writes "ecological investigation levels (EILs)"; PFAS NEMP 3.0 section 8.7.1 writes
    # "ecological soil guideline values" and Table 6 "Ecological guideline values for soil".
    SynonymGroup(
        "ecological investigation level",
        ("ecological investigation level", "eil", "ecological guideline value", "ecological soil guideline value"),
        triggers=(
            "ecological soil value",
            "ecological value",
            "ecological soil guideline",
            "ecological guideline",
            "ecological criteria",
            "ecological soil criteria",
            "ecological level",
            "ecological soil level",
            "soil ecological value",
        ),
    ),
    SynonymGroup("ecological screening level", ("ecological screening level", "esl")),
    SynonymGroup("recreational water", ("recreational water", "recreation", "swimming")),
    SynonymGroup("TOP assay", ("total oxidisable precursor", "total oxidizable precursor", "top assay")),
    SynonymGroup("classification", ("classification", "classify", "classified")),
    SynonymGroup("mandatory auditor", ("mandatory auditor", "contaminated sites auditor", "auditor")),
    SynonymGroup("tolerable daily intake", ("tolerable daily intake", "tdi")),
    SynonymGroup("contaminated sites act", ("contaminated sites act", "cs act")),
    SynonymGroup(
        "timeframe",
        ("timeframe", "time frame", "how long", "how soon"),
        triggers=("wait", "waiting", "when do i", "when do we", "when must", "by when", "deadline", "how many days"),
    ),
    # DWER identification and reporting guideline section 6.1: "the following persons have a duty to report a site".
    SynonymGroup(
        "duty to report",
        ("duty to report", "required to report", "must report", "obliged to report"),
        triggers=("have to report", "has to report", "need to report", "needs to report", "obligation to report"),
    ),
    SynonymGroup("land use", ("land use", "land uses")),
    # PFAS NEMP 3.0 section 18.2.1: "use polypropylene or high-density polyethylene (HDPE) sample containers";
    # Appendix B.3.6: "Appropriately prepared bottles should be supplied by the analytical laboratory".
    SynonymGroup(
        "sample container",
        (
            "sample container",
            "sampling container",
            "sample bottle",
            "sampling bottle",
            "bottle",
            "hdpe",
            "high density polyethylene",
            "polypropylene",
            "sample jar",
        ),
        triggers=("container", "containers", "sample containers", "bottles", "jars", "vials", "vial"),
    ),
    # PFAS NEMP 3.0 Appendix B.3.6: "place on ice in a cooler box or fridge to rapidly chill the sample to below
    # 4 C"; DWER assessment guideline Appendix A: "sample preservation methods", "Holding times".
    SynonymGroup(
        "sample storage",
        (
            "sample storage",
            "sample preservation",
            "preservation",
            "preserved",
            "holding time",
            "chill",
            "on ice",
            "cooler box",
            "fridge",
        ),
        triggers=(
            "store",
            "storing",
            "samples stored",
            "sample stored",
            "samples kept",
            "keep samples",
            "preserve",
            "preserving",
            "preservative",
            "refrigerate",
            "refrigerated",
            "chilled",
            "held",
            "hold",
            "holding",
        ),
    ),
    # PFAS NEMP 3.0 section 18.2.1: "Conventional groundwater drilling and well development practices are generally
    # suitable for monitoring wells"; Appendix B.3.6: "Groundwater wells should be purged before sampling".
    SynonymGroup(
        "monitoring well",
        ("monitoring well", "monitoring bore", "bore", "well development"),
        triggers=("boreholes", "piezometer", "piezometers"),
    ),
    SynonymGroup(
        "well development",
        ("well development", "developed well", "bore development", "develop the well"),
        triggers=("developing a well", "develop a well", "developing the well", "developed the well"),
    ),
    # PFAS NEMP 3.0 Appendix B.3.6: "low-flow purging", "purging has removed all stagnant water from the bore".
    SynonymGroup("purging", ("purge", "purging", "purged", "low flow", "micro purge", "stagnant water")),
    # PFAS NEMP 3.0 Appendix B.3.6: "water quality parameters show stable readings", "the time taken for
    # parameters to stabilise". The bare word is not searched: "stabilisation" is also a remediation technique.
    SynonymGroup(
        "parameter stabilisation",
        ("stable readings", "parameters to stabilise", "parameters stabilise", "stable water quality parameters"),
        triggers=(
            "parameters stabilise",
            "parameters stabilize",
            "parameter stabilisation",
            "parameter stabilization",
            "stable parameters",
            "field parameters",
            "water quality parameters",
        ),
    ),
    # DWER assessment guideline section 9.4: "There is no minimum number of sampling points recommended for a given
    # size of site"; Appendix A: "Rationale for selection of sampling density".
    SynonymGroup(
        "number of samples",
        (
            "number of samples",
            "number of sampling points",
            "number of sampling locations",
            "minimum number of samples",
            "sampling density",
        ),
        triggers=(
            "how many samples",
            "how many soil samples",
            "how many water samples",
            "how many sampling points",
            "how many sampling locations",
            "how many boreholes",
            "how many bores",
            "number of soil samples",
            "sample density",
            "soil sampling density",
        ),
    ),
    # DWER assessment guideline section 9.4: "for a given size of site"; Appendix C: "size of the area to be
    # sampled"; PFAS NEMP 3.0 section 8.7.1: "each hectare".
    SynonymGroup(
        "site size",
        ("size of site", "size of the site", "site size", "size of the area", "area to be sampled", "hectare"),
        triggers=("hectares", "ha", "square metres", "square meters", "m2", "acre", "acres", "site area"),
    ),
    # --- Plain-language equivalents (added 2026-09-24 for casual questions). Each group maps an everyday word that
    # no document uses, or uses rarely, to the words the documents write.
    # ASC NEPM Schedule B1 Table 1A(1) notes: HIL A is for residential sites with "home-grown produce"; PFAS NEMP 3.0
    # section 8.6: "consumption of home-grown produce"; DWER 2021 section 10.7: "some edible produce".
    SynonymGroup(
        "home-grown produce",
        (
            "home grown produce",
            "home grown",
            "edible produce",
            "fruit and vegetable",
            "fruit and vegetables",
            "vegetable",
        ),
        triggers=(
            "veg",
            "veggie",
            "veggies",
            "vegie",
            "vegies",
            "vegetables",
            "fruit and veg",
            "veggie patch",
            "vegie patch",
            "vegetable patch",
            "vegetable garden",
            "grow veg",
            "grow vegetables",
            "grow food",
            "grow their own",
            "grow your own",
            "grow my own",
            "growing veg",
            "growing vegetables",
            "growing food",
            "homegrown",
        ),
    ),
    # ASC NEPM Schedule B1 section 2.1.2: "investigation and screening levels are not clean-up or response levels";
    # DWER 2021 section 12: "remediation", "remediation goals".
    SynonymGroup(
        "remediation",
        ("remediation", "clean up", "cleanup"),
        triggers=("clean ups", "cleaning up", "clean it up", "remediate", "remediating", "remediated"),
    ),
    # ASC NEPM Schedule B1 section 2.1.2: "default remediation criteria"; DWER 2021 section 12: "remediation goals".
    SynonymGroup(
        "remediation criteria",
        (
            "remediation criteria",
            "remediation goals",
            "remediation targets",
            "clean up criteria",
            "clean up levels",
            "response levels",
        ),
        triggers=(
            "remediation target",
            "remediation goal",
            "remediation standards",
            "clean up targets",
            "clean up target",
            "clean up goals",
            "clean up standards",
            "clean up level",
        ),
    ),
    # DWER 2025 identification, reporting and classification guideline section 6.3.1: a "prescribed form" ('Form 1')
    # "together with copies of all supporting information"; section 10.1: disclosure "on the prescribed form (Form
    # 6)"; DWER 2021 Appendix A: "documentation".
    SynonymGroup(
        "forms and documentation",
        ("prescribed form", "form", "documentation", "records"),
        triggers=("paperwork", "paper work", "forms", "form to fill", "forms to fill", "documents to submit"),
    ),
    # DWER 2021 section 11.7: "notify the DoH Environmental Health Directorate".
    SynonymGroup(
        "notify",
        ("notify", "notified", "notification"),
        triggers=("tell", "tells", "telling", "let know", "let them know", "inform", "informed", "alert"),
    ),
    # PFAS NEMP 3.0 section 18: samples "analysed" by the laboratory; DWER 2021 section 9: "tested", "sampled".
    SynonymGroup(
        "tested",
        ("tested", "analysed", "sampled"),
        triggers=("test", "tests", "testing", "analyse", "analyze", "analyzed", "get tested", "lab test", "lab tests"),
    ),
    # DWER 2021 section 11.7: concentrations "detected"; the everyday "picked up" means the same.
    SynonymGroup(
        "detected",
        ("detected", "detections"),
        triggers=("picked up", "pick up", "picks up", "showed up", "shows up", "turned up", "came back with"),
    ),
    # DWER 2025 section 10.1: disclosure to "a purchaser"; section 7.5.6: "a prospective buyer or lessee".
    SynonymGroup("buyer", ("purchaser", "buyer"), triggers=("buyers", "purchasers", "new owner", "new owners")),
    # The bare word is searched as before; the group only lets "dirt" find it. DWER 2021 section 12.9: "Re-use of
    # excavated soil".
    SynonymGroup("soil", ("soil",), triggers=("dirt",)),
    # DWER 2021 section 12.9: "Re-use of excavated soil"; "excavated spoil".
    SynonymGroup(
        "excavation",
        ("excavated", "excavation", "excavate"),
        triggers=("dig", "digs", "digging", "dug", "dig up", "dug up", "dug out", "excavating"),
    ),
    # ASC NEPM Schedule B1 Table 1A(1): "Residential A with garden/accessible soil".
    SynonymGroup("garden", ("garden", "accessible soil"), triggers=("backyard", "backyards", "back yard")),
    # PFAS NEMP 3.0 section 8.6.2: "consumption of livestock, poultry, eggs and fish".
    SynonymGroup("poultry and eggs", ("poultry", "eggs"), triggers=("chooks", "chook", "chickens", "hens", "egg")),
    # PFAS NEMP 3.0 section 8.6.2 and DWER 2021 section 11.7: "consumption of home-grown produce".
    SynonymGroup(
        "consumption", ("consumption", "ingestion"), triggers=("eat", "eats", "eating", "eaten", "consume", "consuming")
    ),
    # ASC NEPM Schedule B1 Table 1A(1) note 1: HIL A "also includes children's day care centres".
    SynonymGroup("children", ("children", "child"), triggers=("kids", "kid", "toddlers", "toddler")),
    # PFAS NEMP 3.0 section 11: "Transport of PFAS-contaminated material", "The transport and tracking of".
    SynonymGroup(
        "transport",
        ("transport", "transported", "transporting"),
        triggers=("truck", "trucks", "trucked", "trucking", "haul", "hauled", "hauling", "cart away", "carted"),
    ),
    # PFAS NEMP 3.0 section 18.2.2: "new clothing, footwear, PPE and treated fabrics".
    SynonymGroup(
        "clothing",
        ("clothing", "clothes", "footwear"),
        triggers=("jacket", "jackets", "rain jacket", "raincoat", "rain coat", "wet weather gear", "boots", "shoes"),
    ),
    # ASC NEPM Schedule B1 Table 5: "Agricultural use, stock watering"; DWER 2021: "livestock drinking water".
    SynonymGroup(
        "stock watering",
        ("stock watering", "livestock drinking water"),
        triggers=(
            "watering livestock",
            "watering stock",
            "livestock watering",
            "water for livestock",
            "water for stock",
            "stock water",
            "livestock drinking",
        ),
    ),
    # ASC NEPM Schedule B1 section 4 and Table 7: "bonded ACM"; DWER 2025: "asbestos cement sheeting". "Fibro" is the
    # everyday Australian word for asbestos cement sheeting.
    SynonymGroup(
        "bonded asbestos",
        ("bonded asbestos", "bonded acm", "asbestos cement", "fibre cement"),
        triggers=("fibro", "bonded fibro", "fibro sheeting", "fibro sheets", "asbestos sheeting", "asbestos sheets"),
    ),
    # ASC NEPM Schedule B1 Table 1A(1): "Residential A with garden/accessible soil".
    SynonymGroup(
        "residential",
        ("residential",),
        triggers=("house", "houses", "house block", "residential block", "suburban block", "housing"),
    ),
    # DWER 2021 section 12: "removing underground storage tanks at a disused service station site".
    SynonymGroup(
        "service station",
        ("service station", "service stations"),
        triggers=("petrol station", "petrol stations", "gas station", "fuel station", "servo"),
    ),
)
"""Phrases are lower case and tokenised the same way as the index (porter stemming means 'levels' also finds
'level', so plurals are not listed)."""

_STOPWORD_TEXT = """
    a about above after again against all also am an and any are as at be been before being below between both but
    by can could did do does doing done during each either few for from further had has have having he her here how
    i if in into is it its itself just me might more most my no nor not now of off on once only or other our out
    over own please same say says said should so some such tell than that the their them then there these they this
    those through to too under until up upon us very was we were what when where whether which while who whom why
    will with within would you your guideline guidelines guidance document documents indexed
    ug ng mg kg l ml per using come comes get gets got make makes want wants go goes going gone went put puts
    need needed needs used use must may shall according another one ones someone something anything thing things
    involve involves involved involving entail entails entailed entailing
    give gives giving gave happen happens happened explain wonder wondering reckon think guess right
    really actually basically probably maybe perhaps pretty quite kind sort bit lot lots stuff even ever already yet
    etc like still much many anyone anybody everyone everybody somebody anywhere
    ok okay hi hey hello thanks thank cheers mate guys wanna gonna gotta
    don doesn didn isn aren wasn weren hasn haven hadn won wouldn shouldn couldn mustn re ve ll
    apply applies applied applying
    ordinary usual usually typical typically normally instead enough
    ask asks asked asking keep keeps kept reckons reckoned sorry dumb silly stupid
    whats wats wots
"""
STOPWORDS = frozenset(_STOPWORD_TEXT.split())
"""Question words and fillers that carry no search meaning. 'guideline' alone is dropped because every document
uses it; 'guideline value' is still found as a synonym phrase before stopwords are removed. 'Involve' and 'entail'
only frame a question ("what does a preliminary site investigation involve?"): as rare words they outweighed the
subject and pulled in a passage that happened to say "involves". 'Involvement' is a different word and is kept.

The second block (added 2026-09-24) is conversation: light verbs ('give'), hedges ('really', 'probably', 'still'),
'anyone', 'like' and greetings. In a chatty question they are the rarest words, so they outweighed the subject. It
also holds the stems contractions leave behind ("don't" is tokenised as 'don' and 't', "we're" as 'we' and 're'),
'apply' in any form ("which values apply" frames a question; "What earthquake design loads apply to buildings?"
matched a passage on the verb), plain hedges ('ordinary', 'usual', 'instead', 'enough') and conversation verbs
('asking', 'keeps', 'reckons', 'sorry'). 'Whats' and its misspellings are "what's" typed without the apostrophe:
searched as a word, 'whats CSM' found only two passages."""

FRAMING_PHRASES: tuple[str, ...] = (
    "can you tell me",
    "could you tell me",
    "can you let me know",
    "tell me about",
    "tell me",
    "let me know",
    "i need to know",
    "need to know",
    "needs to know",
    "want to know",
    "wants to know",
    "wanted to know",
    "i want to know",
    "i d like to know",
    "i would like to know",
    "do you know",
    "does anyone know",
    "anyone know",
    "how do i know",
    "how do we know",
    "how would i know",
    "how will i know",
    "how can i tell",
    "how do i tell",
    "how can we tell",
    "what s the deal with",
    "what is the deal with",
    "what s the story with",
    "what s the go with",
    "where do i find",
    "where can i find",
    "where would i find",
    "where do we find",
    "what s the rule for",
    "what s the rule on",
    "what are the rules for",
    "what are the rules on",
    "is it true that",
    "i was wondering",
    "quick question",
    "any idea",
    "any ideas",
    "in plain english",
    "in simple terms",
    "stand for",
    "stands for",
    "short for",
    "abbreviation for",
)
"""Ways a person frames a question that say nothing about its subject ("can you tell me", "what's the deal with",
"how do I know if", "what does SAQP stand for"). They are removed as whole phrases before anything else, so 'know'
and 'tell' keep their meaning elsewhere ("known contamination", "tell DWER"). Searched as a word, 'stand' matched
"stand-alone report" and pushed out the passages that spell the abbreviation out. 'Mean' is not here: it is also a
statistics term in the guidance ("95% UCL of the mean")."""

PLACE_PHRASES: tuple[str, ...] = ("western australian", "western australia", "australian", "australia", "perth", "wa")
"""Places that only set the scope. Every indexed document is Western Australian or national guidance, so 'in Perth'
or 'in WA' does not say which passage answers a question; as rare words they let a passage that happens to name
Perth carry an off-topic question ("What earthquake design loads apply to buildings in Perth?"). Other states are
handled before the search (:mod:`evidenceline.guidance.scope`)."""

_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class Concept:
    """One idea in the question and the phrases that express it."""

    label: str
    phrases: tuple[str, ...]
    expanded: bool
    """True when the concept came from :data:`SYNONYM_GROUPS`."""

    def fts_expression(self) -> str:
        """FTS5 query text: the phrases OR-ed together, each quoted so FTS5 syntax characters are inert."""
        quoted = [f'"{phrase}"' for phrase in self.phrases]
        return quoted[0] if len(quoted) == 1 else "(" + " OR ".join(quoted) + ")"


def tokenize(text: str) -> list[str]:
    """Lower-case alphanumeric tokens, split the way the index's unicode61 tokenizer splits them."""
    return _TOKEN.findall(text.lower().replace("\u00b5", "u").replace("\u03bc", "u"))


def singular(token: str) -> str:
    """A light plural fold for matching synonym phrases ('levels' -> 'level'); the index does its own stemming."""
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _folded(tokens: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(singular(token) for token in tokens)


_GROUP_PHRASES: list[tuple[tuple[str, ...], int]] = sorted(
    {
        (_folded(tokenize(wording)), group_index)
        for group_index, group in enumerate(SYNONYM_GROUPS)
        for wording in (*group.phrases, *group.triggers)
    },
    key=lambda item: (-len(item[0]), item[1], item[0]),
)
"""Every phrase and trigger with the index of its group, longest first so 'perfluorooctane sulfonic acid' wins over
its parts."""


_IGNORED: list[tuple[str, ...]] = sorted(
    {_folded(tokenize(phrase)) for phrase in (*FRAMING_PHRASES, *PLACE_PHRASES)}, key=lambda item: (-len(item), item)
)


def _mark_ignored(folded: tuple[str, ...], used: list[bool]) -> None:
    """Mark the framing and place phrases in the question as used, so they become neither concepts nor pairs."""
    for phrase in _IGNORED:
        width = len(phrase)
        for start in range(len(folded) - width + 1):
            span = range(start, start + width)
            if folded[start : start + width] == phrase and not any(used[p] for p in span):
                for position in span:
                    used[position] = True


def _group_concept(group_index: int) -> Concept:
    group = SYNONYM_GROUPS[group_index]
    normalised = tuple(dict.fromkeys(" ".join(tokenize(p)) for p in group.phrases))
    return Concept(group.label, normalised, expanded=True)


def _match_groups(
    positions: list[int],
    folded: tuple[str, ...],
    used: list[bool],
    seen: set[int],
    inside: list[bool] | None = None,
) -> list[tuple[int, Concept]]:
    """Synonym groups found in the token sequence ``positions`` (indexes into the question's tokens). When given,
    ``inside`` marks the positions covered by a matched wording of two or more words."""
    found: list[tuple[int, Concept]] = []
    words = [folded[p] for p in positions]
    for phrase, group_index in _GROUP_PHRASES:
        width = len(phrase)
        for start in range(len(words) - width + 1):
            span = positions[start : start + width]
            if any(used[p] for p in span) or tuple(words[start : start + width]) != phrase:
                continue
            for position in span:
                used[position] = True
                if inside is not None and width > 1:
                    inside[position] = True
            if group_index not in seen:
                seen.add(group_index)
                found.append((span[0], _group_concept(group_index)))
    return found


def query_concepts(question: str) -> list[Concept]:
    """The concepts in ``question``, in the order they appear, without duplicates.

    Framing and place phrases (:data:`FRAMING_PHRASES`, :data:`PLACE_PHRASES`) are removed first. Synonym phrases
    and triggers are then matched (longest first) on the token sequence, with plurals folded
    ('investigation levels' finds the 'investigation level' group), then again with stopwords left out; the remaining
    tokens become single-word concepts unless they are stopwords or single characters.
    """
    tokens = tokenize(question)
    folded = _folded(tokens)
    used = [False] * len(tokens)
    _mark_ignored(folded, used)
    seen: set[int] = set()
    found = _match_groups([p for p in range(len(tokens)) if not used[p]], folded, used, seen)
    content = [p for p, token in enumerate(tokens) if token not in STOPWORDS and not used[p]]
    found += _match_groups(content, folded, used, seen)
    seen_words: set[str] = set()
    for position, token in enumerate(tokens):
        if used[position] or token in STOPWORDS or token in seen_words or len(token) < 2:
            continue
        seen_words.add(token)
        found.append((position, Concept(token, (token,), expanded=False)))
    found.sort(key=lambda item: item[0])
    return [concept for _, concept in found]


def question_phrases(question: str) -> list[str]:
    """Neighbouring word pairs in the question that are both meaningful ('site assessment', 'soil samples').

    Pairs inside a synonym phrase of two or more words are left out: the synonym group already searches the whole
    phrase. A one-word synonym ('soil', 'DWER') still pairs with its neighbour ('contaminated soil').
    """
    tokens = tokenize(question)
    folded = _folded(tokens)
    ignored = [False] * len(tokens)
    _mark_ignored(folded, ignored)
    inside = list(ignored)
    _match_groups([p for p in range(len(tokens)) if not ignored[p]], folded, list(ignored), set(), inside)
    pairs: list[str] = []
    for position in range(len(tokens) - 1):
        first, second = tokens[position], tokens[position + 1]
        if inside[position] or inside[position + 1] or first in STOPWORDS or second in STOPWORDS:
            continue
        if len(first) < 2 or len(second) < 2:
            continue
        pair = f"{first} {second}"
        if pair not in pairs:
            pairs.append(pair)
    return pairs
