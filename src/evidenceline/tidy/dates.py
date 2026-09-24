"""Day-first date and time reading for lab, chain-of-custody and field-sheet files.

Australian files write dates day first. A month-first reading is never tried, so ``12/09/25`` is always
12 September 2025 and ``09/13/25`` is an error (there is no month 13), not 13 September.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from evidenceline.errors import EvidencelineError

_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)

_TIME = r"(?:\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?:\s*(?P<ampm>[AaPp][Mm]))?)?"
_ISO = re.compile(r"(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})" + _TIME.replace(r"\s+", r"[ T]"))
_NAMED = re.compile(r"(?P<day>\d{1,2})\s+(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<year>\d{4})" + _TIME)
_NUMERIC = re.compile(r"(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4}|\d{2})" + _TIME)
_CLOCK = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?:\s*(?P<ampm>[AaPp][Mm]))?")

HELP = "Dates are read day first: '12/09/2025', '12/09/25 11:40' or '12 Sep 2025 11:40 AM'."


@dataclass(frozen=True, slots=True)
class Stamp:
    """A date with an optional time of day, as written in one file."""

    date: dt.date
    time: dt.time | None
    written: str

    def iso(self) -> str:
        return self.date.isoformat() if self.time is None else f"{self.date.isoformat()}T{self.time:%H:%M}"

    def agrees_with(self, other: Stamp) -> bool:
        """Same day, and the same minute when both files give a time."""
        if self.date != other.date:
            return False
        return self.time is None or other.time is None or self.time == other.time


def _month_from_name(name: str, text: str) -> int:
    lowered = name.lower()
    for index, full in enumerate(_MONTHS, start=1):
        if len(lowered) >= 3 and full.startswith(lowered):
            return index
    raise EvidencelineError(f"Could not read the date {text!r}: {name!r} is not a month name. {HELP}")


def _clock(hour_text: str | None, minute_text: str | None, ampm: str | None, text: str) -> dt.time | None:
    if hour_text is None or minute_text is None:
        return None
    hour, minute = int(hour_text), int(minute_text)
    if ampm is not None:
        if not 1 <= hour <= 12:
            raise EvidencelineError(f"Could not read the time in {text!r}: hour {hour} does not go with AM or PM.")
        hour = hour % 12 + (12 if ampm.lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        raise EvidencelineError(f"Could not read the time in {text!r}: {hour_text}:{minute_text} is not a time of day.")
    return dt.time(hour, minute)


def _build(year: int, month: int, day: int, text: str) -> dt.date:
    if not 1 <= month <= 12:
        raise EvidencelineError(
            f"Could not read the date {text!r}: there is no month {month}. The month is the second number. {HELP}"
        )
    try:
        return dt.date(year, month, day)
    except ValueError as exc:
        raise EvidencelineError(f"Could not read the date {text!r}: {exc}. {HELP}") from exc


def parse_stamp(text: str) -> Stamp:
    """Read a date with an optional time. Raises EvidencelineError with the accepted forms."""
    cleaned = " ".join(text.split())
    if not cleaned:
        raise EvidencelineError(f"A date is missing. {HELP}")
    match = _ISO.fullmatch(cleaned)
    if match:
        date = _build(int(match["year"]), int(match["month"]), int(match["day"]), text)
    elif (match := _NAMED.fullmatch(cleaned)) is not None:
        date = _build(int(match["year"]), _month_from_name(match["mon"], text), int(match["day"]), text)
    elif (match := _NUMERIC.fullmatch(cleaned)) is not None:
        year = int(match["year"])
        date = _build(year + 2000 if year < 100 else year, int(match["month"]), int(match["day"]), text)
    else:
        raise EvidencelineError(f"Could not read the date {text!r}. {HELP}")
    return Stamp(date=date, time=_clock(match["hour"], match["minute"], match["ampm"], text), written=text.strip())


def parse_date(text: str) -> dt.date:
    """Read a date (any time given is ignored)."""
    return parse_stamp(text).date


def parse_clock(text: str) -> dt.time | None:
    """Read a time of day such as ``09:10`` or ``01:40 PM``. An empty cell gives None."""
    cleaned = text.strip()
    if not cleaned:
        return None
    match = _CLOCK.fullmatch(cleaned)
    if match is None:
        raise EvidencelineError(f"Could not read the time {text!r}. Use hh:mm, for example 09:10 or 13:40.")
    return _clock(match["hour"], match["minute"], match["ampm"], text)
