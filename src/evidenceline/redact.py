"""Guard rail: client and site identifiers are replaced with placeholders before any text reaches a model.

Identifiers come from two places:

* a local TOML file (path from ``EVIDENCELINE_REDACT``, else ``~/.evidenceline/redact.toml``) listing client names,
  site names, people, addresses, lots, emails and phone numbers. See ``examples/redact.example.toml``. Instead of a
  path, ``EVIDENCELINE_REDACT`` may name a packaged file: ``builtin:fds01-demo`` lists the fictional client name and
  site address of the sample site FDS-01, for the hosted demo (see :data:`BUILTIN_FILES`);
* built-in patterns for WA lot numbers ("Lot 123"), street addresses ending in a suburb and "WA", email addresses
  and Australian phone numbers.

Each identifier becomes a stable placeholder such as ``[CLIENT-1]`` or ``[ADDRESS-2]``: the same identifier gets
the same placeholder for the whole session. The map back to the raw values stays in this process's memory and is
used only by :func:`restore`, for local exports. It is never returned by a tool and never written to disk. Every
redaction appends one line to a local JSONL audit log (path from ``EVIDENCELINE_REDACT_LOG``, else
``~/.evidenceline/redaction-audit.jsonl``) with the time, the tool and counts per placeholder type, never a value.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tomllib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal, cast

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from evidenceline.errors import EvidencelineError

CONFIG_ENV = "EVIDENCELINE_REDACT"
AUDIT_ENV = "EVIDENCELINE_REDACT_LOG"
BUILTIN_PREFIX = "builtin:"
"""``EVIDENCELINE_REDACT=builtin:<name>`` loads a packaged identifier file instead of a file on disk."""


@dataclass(frozen=True, slots=True)
class BuiltinFile:
    """An identifier file shipped inside the package, for a demo that has no identifier file of its own."""

    resource: tuple[str, ...]
    """Path parts under ``evidenceline/data``."""
    description: str
    """What it lists, in words, for show_redactions and the hosted connector's note."""


BUILTIN_FILES: dict[str, BuiltinFile] = {
    "fds01-demo": BuiltinFile(
        ("fds01_site", "redact.demo.toml"),
        "the built-in demo identifier file, which lists only the fictional client name and site address of the sample "
        "site FDS-01",
    ),
}
"""Packaged identifier files by name. Each lists made-up identifiers only."""

PlaceholderType = Literal["CLIENT", "SITE", "PERSON", "ADDRESS", "LOT", "EMAIL", "PHONE"]

_Key = tuple[PlaceholderType, str, str]
"""(placeholder type, identity key, raw value used by restore)."""

CONFIG_TYPES: dict[str, PlaceholderType] = {
    "client": "CLIENT",
    "site": "SITE",
    "person": "PERSON",
    "address": "ADDRESS",
    "lot": "LOT",
    "email": "EMAIL",
    "phone": "PHONE",
}
"""TOML table names and the placeholder type each one produces."""

_MIN_NAME_LENGTH = 2

# --- built-in patterns ---------------------------------------------------------------------------------------------

_EMAIL = re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])")

# Addresses are matched in any letter case: field sheets and title documents often write them in capitals.
_STREET_TYPE = (
    r"(?:Road|Rd|Street|St|Avenue|Ave|Drive|Dr|Highway|Hwy|Way|Place|Pl|Crescent|Cres|Court|Ct|Parade|Pde"
    r"|Terrace|Tce|Lane|Close|Boulevard|Bvd)"
)
_WORD = r"[A-Za-z][A-Za-z'-]*"
_STATE = r"(?:W\.\s?A\b\.?|WA\b|Western\s+Australia\b)"
"""WA, W.A., Wa or Western Australia."""
_NUMBER = r"(?<![\w/-])(?:(?:Lot|Unit)\s+)?\d{1,5}[A-Za-z]?(?:\s*/\s*\d{1,5}[A-Za-z]?)?(?:-\d{1,5}[A-Za-z]?)?,?\s+"
_SUBURB = rf"(?<![\w'-]){_WORD}(?:\s+{_WORD}){{0,2}}"
_SUBURB_FIRST = r"(?-i:(?<![\w'-])[A-Z][A-Za-z'-]*(?:\s+[A-Z][A-Za-z'-]*){0,2})"
"""A suburb at the start of an address: capitalised words only, so the words before it are not swept in."""
_POSTCODE = r"(?:,?\s*6\d{3}\b)?"
_ADDRESS = re.compile(
    # 12 Example Road, Welshpool WA 6106
    rf"(?i:{_NUMBER}(?:{_WORD}\s+){{1,3}}{_STREET_TYPE}\.?,?\s+{_SUBURB},?\s+{_STATE}{_POSTCODE}"
    # Welshpool WA 6106, 12 Example Road
    rf"|{_SUBURB_FIRST},?\s+{_STATE}{_POSTCODE},?\s+{_NUMBER}(?:{_WORD}\s+){{1,3}}{_STREET_TYPE}\b\.?)"
)
_LOT = re.compile(
    r"\b(?:Lot|LOT)\s+\d{1,6}[A-Za-z]?\b"
    r"(?:\s+on\s+(?:Deposited\s+Plan|Strata\s+Plan|Diagram|Plan|DP|SP)\s*\d{1,7}\b)?"
)
_PHONE = re.compile(
    r"(?<![\w+])(?:"
    r"(?:\+61[\s-]?(?:\(0\)\s?)?[2378]|\(0[2378]\)|0[2378])[\s-]?\d{4}[\s-]?\d{4}"  # landline
    r"|(?:\+61[\s-]?4|04)\d{2}[\s-]?\d{3}[\s-]?\d{3}"  # mobile
    r"|1[38]00[\s-]?\d{3}[\s-]?\d{3}"  # 1300 and 1800 numbers
    r")(?!\w)"
)

BUILT_IN_PATTERNS: tuple[str, ...] = (
    "email addresses",
    "street addresses of the form '<number> <Name> Road/Street/Avenue/Drive <Suburb> WA' (with or without postcode, "
    "in any letter case, with WA also written W.A. or Western Australia), or suburb first with the suburb "
    "capitalised: '<Suburb> WA <postcode>, <number> <Name> Road'",
    "WA lot numbers such as 'Lot 123' or 'Lot 123 on Deposited Plan 45678'",
    "Australian phone numbers (landline, mobile, 1300 and 1800)",
)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


_ESCAPE = r"\\(?:[ntr]|x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4})"
"""A character written as an escape (as in a validation error that quotes the input with repr)."""
_INVISIBLE = r"(?:[\u00ad\u200b-\u200f\u2060\ufeff]|\\(?:xad|u00ad|u200[b-f]|u2060|ufeff))*"
"""Soft hyphens, zero-width characters and direction marks, raw or escaped: copied documents carry them in words."""
_URL_ESCAPE = r"%[0-9a-fA-F]{2}"
"""A character written as a URL escape, such as '%20' for a space in a link."""
_JOINER = rf"(?:{_ESCAPE}|{_URL_ESCAPE}|[\W_]){{0,8}}"
"""What may stand between two words of a listed name: nothing, spaces, line breaks, hyphens, underscores, dots,
and the same written as escapes ('\\n', '%20')."""
_LETTER = r"[^\W\d_]"
_LETTER_OR_DIGIT = r"[^\W_]"
_CAMEL_CASE = r"(?-i:(?<=[a-z])(?=[A-Z]))"
"""Between a lower-case and a capital letter, where one word of a code or file name ends and the next starts."""
_NAME_START = (
    rf"(?:(?={_LETTER})(?<!{_LETTER})|(?=\d)(?<!{_LETTER_OR_DIGIT})|{_CAMEL_CASE}"
    r"|(?<=\\[ntr])|(?<=\\x[0-9a-fA-F]{2})|(?<=\\u[0-9a-fA-F]{4}))"
)
"""Where a listed name may start: not inside a word of letters, so 'surface' does not hold 'Ace'. A digit or an
underscore before a name that starts with a letter is a boundary ('DSI_Harbourline', '2025Harbourline'), and so is
a change from lower case to a capital ('dsiHarbourline') and an escape such as '\\n' in quoted text. A name that
starts with a digit must not follow another digit, so '112 Example Road' is not read as '12 Example Road'."""
_NAME_END = rf"(?:(?<={_LETTER})(?!{_LETTER})|(?<=\d)(?!{_LETTER_OR_DIGIT})|(?<!{_LETTER_OR_DIGIT})|{_CAMEL_CASE})"
"""Where a listed name may end, the mirror of :data:`_NAME_START`: 'Harbourline2025', 'Harbourline_DSI.pdf' and
'HarbourlineDSI' end the name; 'Harbourlines' does not."""


def _name_key(text: str) -> str:
    """Letters and digits only, case-folded: 'Harbourline-Logistics' and 'harbourline logistics' share a key."""
    return "".join(re.findall(r"[^\W_]+", re.sub(_ESCAPE, " ", text))).casefold()


def _name_pattern(name: str) -> str:
    """A listed name as a pattern: its words in order, joined by any separator or none, with invisible characters
    allowed inside words. Letter case is ignored where the pattern is used."""
    words = re.findall(r"[^\W_]+", name)
    return _JOINER.join(_INVISIBLE.join(re.escape(char) for char in word) for word in words)


def _phone_key(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if raw.lstrip().startswith("+61"):
        digits = "0" + digits[2:].lstrip("0")
    return digits


# --- configuration -------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Identity:
    """One client, site, person, address, lot, email or phone, with every name it may appear under."""

    type: PlaceholderType
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RedactionConfig:
    identities: tuple[Identity, ...]
    status: str
    """Where the identifiers came from, in words, without the file path."""
    builtin: str | None = None
    """The name of the packaged identifier file in use (a key of :data:`BUILTIN_FILES`), or None."""
    file_loaded: bool = False
    """True when an identifier file was read, even one that lists nothing."""


def _parse_config(payload: dict[str, object]) -> tuple[Identity, ...]:
    identities: list[Identity] = []
    for table, entries in payload.items():
        kind = CONFIG_TYPES.get(table)
        if kind is None:
            # The unknown key is not repeated: someone may have typed a client name as a table name.
            raise EvidencelineError(
                f"The redaction file has a table that is not one of: {', '.join(CONFIG_TYPES)}. Check the spelling."
            )
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in cast(list[object], entries)):
            raise EvidencelineError(f"In the redaction file, {table} must be written as [[{table}]] blocks.")
        for number, entry in enumerate(cast(list[object], entries), start=1):
            where = f"[[{table}]] block {number}"
            if set(cast(dict[str, object], entry)) != {"names"}:
                raise EvidencelineError(f"In the redaction file, {where} must have exactly one key: names.")
            names = cast(dict[str, object], entry)["names"]
            if not isinstance(names, list) or not names:
                raise EvidencelineError(f"In the redaction file, {where}: names must be a non-empty list of text.")
            cleaned: list[str] = []
            for position, name in enumerate(cast(list[object], names), start=1):
                if not isinstance(name, str) or len(_name_key(name)) < _MIN_NAME_LENGTH:
                    raise EvidencelineError(
                        f"In the redaction file, {where}: name {position} must be text with at least "
                        f"{_MIN_NAME_LENGTH} characters that are letters or digits."
                    )
                cleaned.append(name.strip())
            identities.append(Identity(kind, tuple(cleaned)))
    return tuple(identities)


def load_config(path: Path | None = None) -> RedactionConfig:
    """Read the identifier file. A missing default file means built-in patterns only; anything broken is an error.

    A file named by ``EVIDENCELINE_REDACT`` (or passed in) must exist: the guard rail fails closed rather than run
    without the identifiers someone meant to load. ``builtin:<name>`` must name a packaged file. Error messages never
    repeat a value from the file.
    """
    env = os.environ.get(CONFIG_ENV)
    if path is not None:
        where = "the given path"
    elif env is not None:
        if not env.strip():
            # Set but empty, for example a config that templates an unset variable: someone meant to load a file.
            raise EvidencelineError(f"{CONFIG_ENV} is set but empty. Set it to the identifier file, or unset it.")
        if env.strip().casefold().startswith(BUILTIN_PREFIX):
            return load_builtin(env.strip()[len(BUILTIN_PREFIX) :])
        path, where = Path(env.strip()), f"the path in {CONFIG_ENV}"
    else:
        path, where = Path.home() / ".evidenceline" / "redact.toml", "the default location"
    if not path.is_file():
        if where != "the default location":
            raise EvidencelineError(f"No redaction file at {where}. Fix the path or unset {CONFIG_ENV}.")
        return RedactionConfig((), "No identifier file at the default location: built-in patterns only.")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # The OS message can contain the path, which may name a client: say only what kind of failure it was.
        raise EvidencelineError(f"The redaction file at {where} could not be read ({type(exc).__name__}).") from exc
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise EvidencelineError(f"The redaction file at {where} is not valid TOML: {exc}") from exc
    identities = _parse_config(payload)
    return RedactionConfig(identities, f"Identifier file loaded from {where}.", file_loaded=True)


def load_builtin(name: str) -> RedactionConfig:
    """A packaged identifier file by name (see :data:`BUILTIN_FILES`). An unknown name is an error, not a fallback."""
    key = name.strip().casefold()
    builtin = BUILTIN_FILES.get(key)
    if builtin is None:
        # The unknown name is not repeated: it came from the environment and could be anything.
        names = ", ".join(f"{BUILTIN_PREFIX}{known}" for known in BUILTIN_FILES)
        raise EvidencelineError(
            f"{CONFIG_ENV} names a built-in identifier file that does not exist. Use one of: {names}."
        )
    resource = resources.files("evidenceline").joinpath("data", *builtin.resource)
    try:
        payload = tomllib.loads(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise EvidencelineError(
            f"The built-in identifier file {BUILTIN_PREFIX}{key} could not be read ({type(exc).__name__})."
        ) from exc
    identities = _parse_config(payload)
    return RedactionConfig(
        identities, f"Built-in identifier file loaded ({BUILTIN_PREFIX}{key}).", builtin=key, file_loaded=True
    )


def default_audit_log() -> Path:
    env = os.environ.get(AUDIT_ENV)
    return Path(env) if env else Path.home() / ".evidenceline" / "redaction-audit.jsonl"


# --- output model --------------------------------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PlaceholderCount(_Model):
    placeholder: str = Field(description="For example '[CLIENT-1]'.")
    type: PlaceholderType
    replacements: int = Field(description="Times this placeholder replaced an identifier in this session.")


class RedactionSummary(_Model):
    identifier_file: str
    identities_configured: dict[str, int] = Field(description="Identities in the identifier file, per type.")
    built_in_patterns: list[str]
    placeholders: list[PlaceholderCount]
    replacements_by_type: dict[str, int]
    audit_log: str
    explanation: str


# --- the redactor --------------------------------------------------------------------------------------------------


class Redactor:
    """One redaction session: stable placeholders, a private restore map and an audit log."""

    def __init__(self, config: RedactionConfig, audit_log: Path | None) -> None:
        self._config = config
        self._audit_log = audit_log
        self._audit_failures = 0
        self._placeholder: dict[tuple[PlaceholderType, str], str] = {}
        self._raw: dict[str, str] = {}
        self._type_of: dict[str, PlaceholderType] = {}
        self._next: Counter[str] = Counter()
        self._hits: Counter[str] = Counter()
        self._lookup: dict[str, _Key] = {}
        self._phone_lookup: dict[str, _Key] = {}
        named: dict[str, tuple[int, _Key]] = {}
        for index, identity in enumerate(config.identities):
            key: _Key = (identity.type, f"identity:{index}", identity.names[0])
            for name in identity.names:
                self._lookup.setdefault(_name_key(name), key)
                if identity.type == "PHONE":
                    self._phone_lookup.setdefault(_phone_key(name), key)
                named.setdefault(_name_pattern(name), (len(_name_key(name)), key))
        # Longest first, so 'Harbourline Logistics' wins over 'Harbourline' when both are listed.
        ordered = sorted(named.items(), key=lambda item: -item[1][0])
        parts = [pattern for pattern, _ in ordered]
        self._named = [(re.compile(rf"(?i:{pattern})"), key) for pattern, (_, key) in ordered]
        # One pass over the text, so placeholders are numbered in reading order. At the same position the earlier
        # alternative wins: an email before a name inside it, a listed name before a built-in pattern, and an
        # address before the lot number it starts with.
        groups = [("EMAIL", _EMAIL.pattern)]
        if parts:
            names = "|".join(parts)
            groups.append(("CONFIG", rf"(?i:{_NAME_START}(?:{names}){_NAME_END})"))
        groups += [("ADDRESS", _ADDRESS.pattern), ("LOT", _LOT.pattern), ("PHONE", _PHONE.pattern)]
        self._pattern = re.compile("|".join(f"(?P<{name}>{pattern})" for name, pattern in groups))
        self._keyers: dict[str, Callable[[str], _Key]] = {
            "EMAIL": self._built_in("EMAIL", str.casefold),
            "CONFIG": self._configured,
            "ADDRESS": self._built_in("ADDRESS", _name_key),
            "LOT": self._built_in("LOT", _normalise),
            "PHONE": self._built_in("PHONE", _phone_key),
        }

    def _configured(self, raw: str) -> _Key:
        """The identity whose listed name matched ``raw``: the longest listed name that matches all of it."""
        return next(key for pattern, key in self._named if pattern.fullmatch(raw))

    def _built_in(self, kind: PlaceholderType, key: Callable[[str], str]) -> Callable[[str], _Key]:
        """Key a built-in match, reusing a configured identity when the same identifier is listed in the file."""

        def keyed(raw: str) -> _Key:
            known = self._lookup.get(_name_key(raw))
            if known is None and kind == "PHONE":
                known = self._phone_lookup.get(_phone_key(raw))
            return known if known is not None else (kind, key(raw), raw)

        return keyed

    def _placeholder_for(self, kind: PlaceholderType, key: str, raw: str) -> str:
        found = self._placeholder.get((kind, key))
        if found is None:
            self._next[kind] += 1
            found = f"[{kind}-{self._next[kind]}]"
            self._placeholder[(kind, key)] = found
            self._raw[found] = raw
            self._type_of[found] = kind
        return found

    def redact_text(self, text: str, counts: Counter[str] | None = None) -> str:
        """Replace every identifier in ``text``. ``counts`` collects replacements per placeholder type."""
        tally: Counter[str] = counts if counts is not None else Counter()

        def replace(match: re.Match[str]) -> str:
            kind, key, raw = self._keyers[match.lastgroup or ""](match.group(0))
            placeholder = self._placeholder_for(kind, key, raw)
            tally[kind] += 1
            self._hits[placeholder] += 1
            return placeholder

        return self._pattern.sub(replace, text)

    def redact_value[T](self, obj: T, counts: Counter[str] | None = None) -> T:
        """Redact every string inside ``obj`` (dict keys too) without writing an audit line.

        Used on a tool call's arguments before they are validated, so an argument of the wrong type (a list, a
        dict) is redacted as well: the SDK's validation error quotes it with ``repr()``, which would otherwise turn
        a line break inside a name into the two characters backslash and n.
        """
        return cast(T, self._walk(obj, counts if counts is not None else Counter()))

    def _walk(self, obj: object, counts: Counter[str]) -> object:
        if isinstance(obj, str):
            return self.redact_text(obj, counts)
        if isinstance(obj, BaseModel):
            updates = {name: self._walk(getattr(obj, name), counts) for name in type(obj).model_fields}
            return obj.model_copy(update=updates)
        if isinstance(obj, dict):
            items = cast(dict[object, object], obj).items()
            return {self._walk(key, counts): self._walk(value, counts) for key, value in items}
        if isinstance(obj, list):
            return [self._walk(item, counts) for item in cast(list[object], obj)]
        if isinstance(obj, tuple):
            return tuple(self._walk(item, counts) for item in cast(tuple[object, ...], obj))
        if isinstance(obj, frozenset):
            return frozenset(self._walk(item, counts) for item in cast(frozenset[object], obj))
        if isinstance(obj, set):
            return {self._walk(item, counts) for item in cast(set[object], obj)}
        return obj

    def apply[T](self, obj: T, *, tool: str, counts: Counter[str] | None = None) -> T:
        """Redact every string inside ``obj`` (pydantic models, dicts, lists, tuples, sets, strings).

        Dict keys are redacted too. A named tuple comes back as a plain tuple. Other objects (numbers, dates,
        dataclasses) are returned unchanged, so pass tool output as pydantic models or plain containers.
        One audit line is written per call. ``counts`` carries replacements already made for the same tool call
        (for example in its arguments, with :meth:`redact_text`), so the call still writes a single line.
        """
        tally: Counter[str] = Counter(counts) if counts is not None else Counter()
        result = cast(T, self._walk(obj, tally))
        self._audit(tool, tally)
        return result

    def restore(self, text: str) -> str:
        """Put the raw values back, for a local export only. Never send the result to a model."""
        if not self._raw:
            return text
        pattern = re.compile("|".join(re.escape(p) for p in sorted(self._raw, key=len, reverse=True)))
        return pattern.sub(lambda m: self._raw[m.group(0)], text)

    def _audit(self, tool: str, counts: Counter[str]) -> None:
        if self._audit_log is None:
            return
        safe_tool = tool if re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", tool) else "unnamed"
        line = {
            "time": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "tool": safe_tool,
            "counts": {kind: counts[kind] for kind in sorted(counts)},
        }
        try:
            self._audit_log.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line) + "\n")
        except OSError:
            self._audit_failures += 1

    def summary(self) -> RedactionSummary:
        """Placeholder names and counts only. Raw values are never included."""
        configured: Counter[str] = Counter(identity.type for identity in self._config.identities)
        by_type: Counter[str] = Counter()
        rows: list[PlaceholderCount] = []
        for placeholder, kind in self._type_of.items():
            by_type[kind] += self._hits[placeholder]
            rows.append(PlaceholderCount(placeholder=placeholder, type=kind, replacements=self._hits[placeholder]))
        if self._audit_log is None:
            audit = "No audit log is kept in this session."
        elif self._audit_failures:
            audit = f"Audit log is local; {self._audit_failures} line(s) could not be written."
        else:
            audit = "Audit log is local: one line per redaction with the time, tool and counts, never values."
        return RedactionSummary(
            identifier_file=self._config.status,
            identities_configured={kind: configured[kind] for kind in sorted(configured)},
            built_in_patterns=list(BUILT_IN_PATTERNS),
            placeholders=rows,
            replacements_by_type={kind: by_type[kind] for kind in sorted(by_type)},
            audit_log=audit,
            explanation=_explanation(self._config),
        )


def _explanation(config: RedactionConfig) -> str:
    """What the guard rail does in this session, in plain words. Only promises what the loaded config can do."""
    builtin = BUILTIN_FILES.get(config.builtin) if config.builtin is not None else None
    if builtin is not None:
        return (
            f"This server loads {builtin.description}. Those names, and text matching the built-in patterns (WA "
            "street addresses and lots, email addresses and Australian phone numbers), are replaced with "
            "placeholders such as [CLIENT-1] or [ADDRESS-1] before tool output reaches the model. Any other client, "
            "site or person's name is NOT redacted: it reaches the machine running this server as typed. To redact "
            "your own client names, run Evidenceline on your own computer with your own identifier file (see "
            "examples/redact.example.toml)."
        )
    if config.identities:
        return (
            "Identifiers listed in the identifier file (client and site names, people, addresses and so on) and "
            "text matching the built-in patterns are replaced with placeholders such as [CLIENT-1] or [ADDRESS-1] "
            "before tool output reaches the model. The same identifier keeps the same placeholder for the whole "
            "session. The raw values stay in the memory of the machine running this server and are only put back "
            "in local exports. Names that are not in the identifier file and do not match a built-in pattern are "
            "not redacted."
        )
    if config.file_loaded:
        return (
            "The identifier file is loaded but lists no names, so client names, site names and people's names are NOT "
            "redacted: only the built-in patterns run (WA street addresses and lots, email addresses and Australian "
            "phone numbers), and they are replaced with placeholders such as [ADDRESS-1]. Add [[client]], [[site]] "
            "and [[person]] blocks to the file to redact names (see examples/redact.example.toml)."
        )
    return (
        "No identifier file is loaded, so client names, site names and people's names are NOT redacted: only the "
        "built-in patterns run (WA street addresses and lots, email addresses and Australian phone numbers), and "
        "they are replaced with placeholders such as [ADDRESS-1]. Text sent to this server reaches the machine "
        "running it as typed. To redact client names, run Evidenceline on your own computer with an identifier "
        "file (see examples/redact.example.toml)."
    )


# --- the session used by the server --------------------------------------------------------------------------------

_session: Redactor | None = None


def session() -> Redactor:
    """The process-wide redaction session, created on first use from the environment."""
    global _session  # noqa: PLW0603 - one session per server process, by design
    if _session is None:
        _session = Redactor(load_config(), default_audit_log())
    return _session


def reset_session() -> None:
    """Forget every placeholder and reload the identifier file on next use."""
    global _session  # noqa: PLW0603
    _session = None


def apply_redaction[T](obj: T, *, tool: str = "apply_redaction") -> T:
    """Redact ``obj`` with the process-wide session and write one audit line naming ``tool``."""
    return session().apply(obj, tool=tool)


def restore(text: str) -> str:
    """Put raw values back into ``text`` for a local export. Never send the result to a model."""
    return session().restore(text)


# --- MCP tool ------------------------------------------------------------------------------------------------------


def show_redactions() -> RedactionSummary:
    """Show what the redaction guard rail has replaced in this session: placeholder names and counts only.

    Lists each placeholder in use (for example [CLIENT-1], [ADDRESS-1], [LOT-1], [EMAIL-1], [PHONE-1]) with how
    many times it replaced an identifier, the totals per type, how many identities the local identifier file
    defines per type (none when no identifier file is loaded, in which case names are not redacted), and the
    built-in patterns. It never shows the raw values: they stay in the memory of the machine running this server.
    Use the placeholders as they are in any text you write; the person's local export puts the real values back.
    """
    try:
        return session().summary()
    except EvidencelineError as exc:
        raise ToolError(str(exc)) from exc


READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

TOOL_SPECS: tuple[tuple[Callable[..., BaseModel], str], ...] = ((show_redactions, "Show redactions"),)
"""(function, title) pairs, for a server that builds its tool list itself (for example with strict arguments)."""


def register_tools(server: MCPServer) -> None:
    """Add this module's tools to ``server``."""
    for fn, title in TOOL_SPECS:
        server.add_tool(fn, title=title, annotations=READ_ONLY)
