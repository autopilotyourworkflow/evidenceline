"""Tidy extracted text: spacing, bullet and dash characters, and words split across lines. Words are never changed."""

from __future__ import annotations

import re
from collections.abc import Sequence

_SPACES = re.compile(r"[ \t\u00a0\u2000-\u200b\u202f\u205f\u3000]+")
_DASHES = re.compile(r"[\u2012\u2013\u2014\u2015\u2212]")
_BULLET_START = re.compile(r"^\s*[\ufffd\u2022\u25aa\u25cf\u2023\uf0b7\uf0a7]\s*")


def clean_line(line: str) -> str:
    """Normalise spacing, bullets and dash characters in one line of extracted text."""
    line = _BULLET_START.sub("", line)
    line = line.replace("\ufffd", "-")
    line = _DASHES.sub("-", line)
    return _SPACES.sub(" ", line).strip()


def join_lines(lines: Sequence[str]) -> str:
    """Join lines into running text, re-joining words hyphenated across a line break."""
    text = ""
    for line in lines:
        if not text:
            text = line
        elif text.endswith("-") and line[:1].islower() and len(text) > 1 and text[-2].isalpha():
            text = text[:-1] + line
        else:
            text = f"{text} {line}"
    return text
