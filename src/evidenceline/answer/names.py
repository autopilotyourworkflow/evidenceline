"""Find client and site names in a question typed into the website, so they are replaced before any search or model.

The question box has no list of the person's clients, so it looks for the shapes names usually take:

- a company: capitalised words ending in a company word, such as 'Harbourline Logistics Pty Ltd', 'Acme Mining',
  'Coastal Holdings' or 'Redgum Group';
- a name given as a client: 'my client Harbourline', 'client: Redgum', 'for our customer Acme';
- a site: capitalised words ending in a place word, such as 'Harbourline Depot', 'Redgum Quarry' or 'Kwinana
  Terminal'.

Plain regular expressions, no model. What they cannot see is listed in DEVNOTES: a lone name with no company or place
word ('Is Harbourline OK?') is not found. The built-in patterns in :mod:`evidenceline.redact` still catch emails,
addresses, lot numbers and phone numbers.
"""

from __future__ import annotations

import re

from evidenceline.redact import Identity

_CAP = r"[A-Z][A-Za-z0-9&'-]*"
"""A capitalised word, such as 'Harbourline', 'O'Brien' or 'A&B'. A word never takes a full stop, so a name does not
run on into the next sentence ('Client: Redgum. What is ...')."""
_COMPANY_WORD = (
    r"(?:Pty\.?\s+Ltd\.?|Pty\.?\s+Limited|Proprietary\s+Limited|Ltd\.?|Limited|Inc\.?|Incorporated|LLC|PLC|"
    r"Corp\.?|Corporation|Holdings|Group|Company|Co\.|Partners|Trust|Logistics|Consulting|Consultants|Industries|"
    r"Enterprises|Mining|Minerals|Resources|Engineering|Constructions?|Developments?|Properties|Transport|Farms)"
)
_PLACE_WORD = r"(?:Depot|Terminal|Refinery|Airport|Airfield|Airbase|Quarry|Landfill|Yard|Works|Estate|Mine|Farm)"
_COMPANY = re.compile(rf"\b(?:{_CAP}\s+){{1,5}}{_COMPANY_WORD}(?![\w])")
_PLACE = re.compile(rf"\b(?:{_CAP}\s+){{1,3}}{_PLACE_WORD}\b")
_AS_CLIENT = re.compile(
    rf"\b(?i:client|customer|landowner|site\s+owner)s?\s*(?i:is\s+|called\s+|named\s+|[:,]\s*)?"
    rf"(?P<name>{_CAP}(?:\s+{_CAP}){{0,4}})"
)

_NOT_A_NAME = frozenset(
    {
        "a", "an", "the", "for", "at", "in", "on", "of", "by", "from", "with", "to", "and", "or", "if", "is", "are",
        "was", "were", "does", "do", "did", "can", "could", "should", "would", "will", "may", "might", "must",
        "what", "when", "where", "which", "who", "whom", "why", "how", "my", "our", "their", "your", "his", "her",
        "its", "this", "that", "these", "those", "please", "hi", "hello", "dear", "i", "we", "you", "they",
    }
)  # fmt: skip
"""Capitalised words that start a question or a clause, not a name: 'Does Acme Pty Ltd ...' keeps 'Does'."""
_GENERIC = frozenset(
    {
        "working", "national", "australian", "western", "state", "federal", "government", "department",
        "environmental", "environment", "water", "waste", "class", "inert", "putrescible", "contaminated",
        "council", "regional", "municipal", "fire", "service", "services", "pfas", "dwer", "epa", "nemp", "adwg",
        "nhmrc", "hepa", "defence", "public",
    }
)  # fmt: skip
"""Words that make a match a kind of thing rather than a name: 'Working Group', 'Water Corporation',
'Class III Landfill'."""


def _trim(name: str) -> str:
    words = name.split()
    while words and words[0].casefold().strip(".,") in _NOT_A_NAME:
        words.pop(0)
    return " ".join(words)


def _is_name(name: str, *, labelled: bool) -> bool:
    """A company or place match needs a word before the company or place word, and no generic word. A name the
    question calls a client is taken as it is."""
    words = [w.casefold().strip(".,") for w in name.split()]
    if labelled:
        return bool(words)
    return len(words) >= 2 and not any(w in _GENERIC for w in words)


def find_names(question: str) -> tuple[Identity, ...]:
    """Client and site names in ``question``, as identities for :class:`evidenceline.redact.Redactor`."""
    clients: list[str] = []
    sites: list[str] = []
    for match in _COMPANY.finditer(question):
        name = _trim(match.group(0))
        if _is_name(name, labelled=False):
            clients.append(name)
    for match in _AS_CLIENT.finditer(question):
        name = _trim(match.group("name"))
        if _is_name(name, labelled=True):
            clients.append(name)
    for match in _PLACE.finditer(question):
        name = _trim(match.group(0))
        if _is_name(name, labelled=False):
            sites.append(name)
    found = [Identity("CLIENT", (name,)) for name in dict.fromkeys(clients)]
    found += [Identity("SITE", (name,)) for name in dict.fromkeys(sites) if name not in clients]
    return tuple(found)
