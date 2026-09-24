"""The result of one question to "Ask the guidelines": what the website and the web API return.

Every field is plain data (strings, integers, booleans, lists), so the result serialises to the same JSON in the
live API and in the answers prepared in advance. Guideline values are strings holding exact decimals in ug/L.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AnswerStatus = Literal["answered", "passages_only", "not_covered", "guard_rail", "about", "paused", "error"]
"""What happened to the question.

- ``answered``: a model wrote an answer from the passages and it passed every check.
- ``passages_only``: the passages are shown without an answer (live answers are off, or the answer failed a check).
- ``not_covered``: the indexed guidance does not appear to cover the question.
- ``guard_rail``: the question asks for a verdict Evidenceline does not give (is the site contaminated, is the
  water safe); a fixed reply and the relevant passages are shown.
- ``about``: the message is a greeting, thanks, or a question about Evidenceline itself ('how does this work?'); a
  fixed reply, with no search and no model call.
- ``paused``: live answers are paused (a spending or rate limit was reached); the passages are shown.
- ``error``: something failed; the explanation says what, in plain English.
"""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Citation(_Model):
    """One passage from the indexed guidance, numbered as the answer cites it."""

    number: int = Field(description="The number the answer uses, as in [1].")
    cited: bool = Field(description="Whether the answer cites this passage.")
    document_id: str
    document: str = Field(description="Document title.")
    edition: str
    wa_status: str
    printed_page: str | None
    pdf_page: int | None
    location: str = Field(description="One line a person can follow, for example 'p. 29 (PDF p. 34), 9. ...'.")
    section: str | None
    excerpt: str = Field(description="A short extract, within the document's licence limit.")
    licence: str
    notice: str
    link: str = Field(description="Official URL, with '#page=N' for PDFs.")


class GuidelineValue(_Model):
    """One drinking-water guideline value, from Evidenceline's verified table (never from extracted text)."""

    marker: str = Field(description="How the answer cites it, for example 'G1'.")
    rule: str = Field(description="'nemp-3.0' or 'current'.")
    rule_name: str
    analyte: str = Field(description="The analyte the question asked about.")
    available: bool = Field(description="False when this rule has no value for the analyte.")
    compared_quantity: str | None = Field(description="What is compared with the value, for example 'PFOS'.")
    value: str | None = Field(description="Exact value in ug/L, or null when the rule has no value.")
    unit: str | None
    source_document: str
    table: str
    page: str
    page_basis: str
    wa_status: str
    note: str = Field(description="One line in plain English.")


class VerificationCheck(_Model):
    name: str
    passed: bool
    detail: str = Field(description="What was checked, and what failed if anything did.")


class Verification(_Model):
    """The deterministic checks run on a model's answer before anyone sees it."""

    ran: bool = Field(description="False when there was no model answer to check.")
    passed: bool | None = Field(description="True only when every check passed; null when nothing ran.")
    checks: list[VerificationCheck]
    summary: str


class AnswerResult(_Model):
    """One question to "Ask the guidelines" and what came back."""

    question: str = Field(description="The question as searched, after redaction.")
    question_redactions: int = Field(description="Identifiers in the question replaced with placeholders.")
    status: AnswerStatus
    explanation: str = Field(description="What this result means, in plain English.")
    answer: str | None = Field(
        description="The checked answer, with [n] citations; null unless status is answered, guard_rail or about."
    )
    citations: list[Citation]
    guideline_values: list[GuidelineValue]
    notes: list[str]
    verification: Verification
    model: str | None = Field(description="The model that wrote the answer, or null when none was called.")
