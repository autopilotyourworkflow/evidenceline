"""Greetings, thanks and questions about Evidenceline itself get a fixed reply, with no search and no model.

Every real question must still reach the guidance search: every question in the three evaluation sets and every
prepared question on the website is checked here, and none may be read as a question about Evidenceline.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from evidenceline.answer import FakeClient, answer
from evidenceline.answer import pipeline as pipeline_module
from evidenceline.answer.pipeline import ABOUT_EXPLANATION, ABOUT_REPLY, THANKS_REPLY
from evidenceline.answer.routing import about_evidenceline, asks_for_verdict
from evidenceline.answer.wording import DASHES
from evidenceline.guidance.models import GuidanceSearch

ROOT = Path(__file__).resolve().parents[1]
WAVE = chr(0x1F44B)
THUMBS_UP = chr(0x1F44D)

ABOUT = [
    "Hello, how does this work?",
    "hello how does this work",
    "How does this work?",
    "How does it work exactly?",
    "So how does this thing work?",
    "how's it work",
    "What is this?",
    "What's this for?",
    "whats this",
    "What is Evidenceline?",
    "What is this website?",
    "What's the point of this tool?",
    "What does Evidenceline do?",
    "What can you do?",
    "What can I ask?",
    "What should I ask here?",
    "What questions can I ask?",
    "What do you know about?",
    "How do I use this?",
    "How to use",
    "Where do I start?",
    "Who made this?",
    "Who are you?",
    "Are you an AI?",
    "Is this ChatGPT?",
    "Is this Claude?",
    "What model are you?",
    "Is this working?",
    "Help",
    "Help me please",
    "Can you help me?",
    "How can you help me?",
    "Tell me about yourself",
    "Explain this tool",
    "Test",
    "testing 123",
    "Hi",
    "Hey there!",
    "Good morning",
    "G'day",
    "Hi! What is Evidenceline? How does it work?",
    "Hello, what model are you?",
    "Hi \u2014 how does this work?",
    "Hello. What\u2019s this?",
    # Found by the review on 24 Sep 2026: emoji, stretched greetings, small talk, fillers before a comma, and
    # questions about what it can answer, whether to trust it, its sources, examples, cost and who made it.
    "hi " + WAVE,
    "hello :)",
    WAVE,
    "??",
    "Hello " + WAVE + " how does this work?",
    "hii",
    "heyy",
    "hey hey",
    "um hello",
    "Hi, how are you?",
    "anyone there?",
    "What am I looking at?",
    "What's this all about?",
    "How exactly does this work?",
    "What is this, exactly?",
    "Sorry, what is this?",
    "What are you?",
    "What kind of questions can you answer?",
    "What kinds of questions can I ask?",
    "Can I ask anything?",
    "What can I ask about?",
    "What topics do you cover?",
    "Give me an example question please",
    "Any examples?",
    "How accurate is this?",
    "Can I trust this?",
    "Does it make mistakes?",
    "What documents do you use?",
    "Where do the answers come from?",
    "Is this real AI?",
    "Is this AI-powered?",
    "Am I talking to a bot?",
    "Are you a person or a bot?",
    "Can you explain how this works?",
    "How does this system work?",
    "What can this tool do for me?",
    "Who is this for?",
    "What am I supposed to do?",
    "Where do I type?",
    "Is this free?",
    "Who is behind this?",
    "How was this built?",
    "Hi, I'm new here",
    "Just browsing",
    "Hello, first time here",
    "how dose this work",
    "What is Evidence Line?",
    "What's it do?",
    "Is this a demo?",
    "What?",
    "?",
    "What is this site?",
    "How does this site work?",
    # naming it (24 Sep 2026: "Hi. How do I use this? And what is this called?")
    "Hi. How do I use this? And what is this called?",
    "What's it called?",
    "What is the name of this tool?",
    "What's your name?",
    "What are you called?",
    "Does it have a name?",
    # No indexed document covers a hazard index, so a bare "HI" in capitals is a greeting too.
    "HI",
    "HI?",
]

THANKS = [
    "Thanks!",
    "Thank you so much",
    "ok thanks",
    "Cheers",
    "Great, thanks",
    "Got it",
    "Hi, thanks",
    "thanks " + THUMBS_UP,
    "thank u",
    "tysm",
    "thanks for your help",
    "Thanks, that helps",
    "ok got it thanks",
    "bye",
    "Makes sense",
    "ok",
]

NOT_ABOUT = [
    "How does PFAS move in groundwater?",
    "How does a tier 1 assessment work?",
    "How does it work for soil samples?",
    "Hi, what is the PFOS limit?",
    "Hello, what is a conceptual site model?",
    "Can you help me find the PFOS limit?",
    "Help with DSI report contents",
    "What is this guideline value for PFOS?",
    "What is it that triggers a DSI?",
    "What is the site?",
    "What is this site's classification?",
    "Is this site contaminated?",
    "Hello, is this site contaminated?",
    "Is it safe to drink?",
    "What does it mean if PFOS is above 0.07?",
    "Who wrote the PFAS NEMP?",
    "What can I do if PFOS is above the limit?",
    "What should I do next at MB2?",
    "How do I use the HIL A table?",
    "What do you know about PFOS in soil?",
    "Thanks, and what about PFHxS?",
    "Thanks. What is the PFHxS value?",
    "Test the MB2 results against the PFOS limit",
    "What are the NSW rules for PFAS in soil?",
    "hello " * 50 + "how does this work?",
    "",
    # A site, not the website: site history is a normal investigation question.
    "Tell me about this site",
    "Describe this site",
    "Who owns this site?",
    "Who developed this site?",
    "How was this site used?",
    "What is this site used for?",
    "What is a HI?",
    "HI > 1?",
    "What's the deal with auditors?",
    "Can you tell me what Queensland says about PFAS in soil?",
    "What data do you have on PFOS?",
    "How accurate are the sampling methods?",
    "What documents do I need for a DSI?",
    "Example of a CSM?",
]


@pytest.mark.parametrize("question", ABOUT)
def test_questions_about_evidenceline(question: str) -> None:
    found = about_evidenceline(question)
    assert found is not None, question
    assert found.kind == "about", question


@pytest.mark.parametrize("question", THANKS)
def test_thanks(question: str) -> None:
    found = about_evidenceline(question)
    assert found is not None, question
    assert found.kind == "thanks", question


@pytest.mark.parametrize("question", NOT_ABOUT)
def test_real_questions_are_not_about_evidenceline(question: str) -> None:
    assert about_evidenceline(question) is None, question


def _eval_questions() -> list[str]:
    golden = json.loads((ROOT / "evals" / "guidance_golden.json").read_text(encoding="utf-8"))
    found = [q["question"] for q in golden["in_scope"] + golden["out_of_scope"]]
    for name in ("guidance_heldout.json", "guidance_heldout2.json"):
        found += [q["question"] for q in json.loads((ROOT / "evals" / name).read_text(encoding="utf-8"))]
    prepared = json.loads((ROOT / "web" / "public" / "data" / "answers.json").read_text(encoding="utf-8"))
    return found + [a["question"] for a in prepared["answers"]]


def test_no_evaluation_or_prepared_question_is_read_as_about_evidenceline() -> None:
    questions = _eval_questions()
    assert len(questions) > 100
    caught = [q for q in questions if about_evidenceline(q) is not None]
    assert caught == []


def test_the_example_in_the_reply_is_a_real_question() -> None:
    found = re.search(r'ask your own, such as "([^"]+)"', ABOUT_REPLY)
    assert found is not None
    example = found.group(1)
    assert example == "How should groundwater samples be collected?"
    assert about_evidenceline(example) is None
    assert not asks_for_verdict(example)


@pytest.fixture
def no_search(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def fake_search(question: str, k: int = 5, *, index_path: Path | None = None) -> GuidanceSearch:
        seen.append(question)
        raise AssertionError("the guidance was searched")

    monkeypatch.setattr(pipeline_module, "search_guidelines", fake_search)
    return seen


def test_about_reply_calls_no_search_and_no_model(tmp_path: Path, no_search: list[str]) -> None:
    client = FakeClient(reply="unused")
    result = answer("Hello, how does this work?", client, index_path=tmp_path / "unused.sqlite")
    assert result.status == "about"
    assert result.answer == "Hello. " + ABOUT_REPLY
    assert result.explanation == ABOUT_EXPLANATION
    assert result.citations == []
    assert result.guideline_values == []
    assert result.notes == []
    assert result.model is None
    assert not result.verification.ran
    assert client.calls == []
    assert no_search == []


def test_no_hello_without_a_greeting_and_thanks_get_their_own_reply(tmp_path: Path, no_search: list[str]) -> None:
    client = FakeClient(reply="unused")
    assert answer("What can I ask?", client, index_path=tmp_path / "x").answer == ABOUT_REPLY
    thanks = answer("Thanks!", client, index_path=tmp_path / "x")
    assert (thanks.status, thanks.answer) == ("about", THANKS_REPLY)
    assert client.calls == []
    assert no_search == []


def test_fixed_replies_are_plain() -> None:
    for text in (ABOUT_REPLY, THANKS_REPLY, ABOUT_EXPLANATION):
        assert not any(dash in text for dash in DASHES)
    assert "contaminated" in ABOUT_REPLY
    assert "scientist who signs" in ABOUT_REPLY
