"""The live answer pipeline for the website's "Ask the guidelines" box.

:func:`answer` searches the indexed public guidance, asks a model for a short cited answer from those passages
only, and checks that answer in code before anyone sees it. See :mod:`evidenceline.answer.pipeline`.
"""

from evidenceline.answer.clients import (
    AnthropicClient,
    ClaudeCliClient,
    FakeClient,
    ModelClient,
    ModelFailedError,
    ModelPausedError,
    ModelReply,
    ModelUnavailableError,
)
from evidenceline.answer.models import AnswerResult, AnswerStatus
from evidenceline.answer.pipeline import answer

__all__ = [
    "AnswerResult",
    "AnswerStatus",
    "AnthropicClient",
    "ClaudeCliClient",
    "FakeClient",
    "ModelClient",
    "ModelFailedError",
    "ModelPausedError",
    "ModelReply",
    "ModelUnavailableError",
    "answer",
]
