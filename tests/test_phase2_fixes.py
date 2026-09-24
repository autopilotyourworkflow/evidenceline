"""Regression tests for the phase 2 tester's findings on the tools, search, evaluation and pre-publish scan.

Each test names the finding it guards. No network, no model call, nothing published.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import shutil
import sys
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from evidenceline import redact, verification
from evidenceline.core import compare_rules, lookup_limit
from evidenceline.dataset import default_dataset
from evidenceline.errors import EvidencelineError
from evidenceline.guidance import evaluate
from evidenceline.guidance.synonyms import STOPWORDS, query_concepts
from evidenceline.redact import RedactionConfig, Redactor
from evidenceline.screening import FOOTNOTE_A, screen_limit, screen_member, screens_for_rule
from evidenceline.tidy.criteria import water_criteria

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "src" / "evidenceline" / "data"
DASHES = ("\u2013", "\u2014")


def _load_script(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"{name}_fixes", REPO / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------- 1. NEMP 3.0 Table 4 footnote a: the sum value also applies to PFOS alone and PFHxS alone ----------


def test_guidelines_note_follows_footnote_a() -> None:
    rules = json.loads((DATA / "guidelines.json").read_text(encoding="utf-8"))["rules"]
    nemp = next(r for r in rules if r["id"] == "nemp-3.0")
    note = next(limit["note"] for limit in nemp["limits"] if limit["key"] == "PFOS+PFHxS")
    assert "PFOS on its own, PFHxS on its own, and the sum of the two" in note
    assert "footnote a" in note
    assert "no separate value" not in note
    assert not any(d in note for d in DASHES)


@pytest.mark.parametrize("analyte", ["PFOS", "PFHxS"])
def test_lookup_limit_says_the_sum_value_applies_to_the_member_alone(analyte: str) -> None:
    out = lookup_limit(analyte, "nemp-3.0")
    assert out.value == "0.07"
    assert f"also applies to {analyte} on its own" in out.caveats[0]
    assert f'"{FOOTNOTE_A}"' in out.caveats[0]
    text = out.model_dump_json()
    assert "no separate value" not in text
    assert f"no value for {analyte}" not in text


def test_footnote_quote_is_verbatim_in_both_verification_passes() -> None:
    pass_a = (DATA / "verification" / "pass-a.json").read_text(encoding="utf-8")
    pass_b = (DATA / "verification" / "pass-b.json").read_text(encoding="utf-8")
    assert FOOTNOTE_A in pass_a
    assert FOOTNOTE_A in pass_b


def test_compare_rules_screens_each_member_alone_under_nemp() -> None:
    result = compare_rules("MB2", "2024-03-12")
    nemp = next(r for r in result.rules if r.rule == "nemp-3.0")
    quantities = [s.compared_quantity for s in nemp.screens]
    assert quantities[0] == "sum of PFOS and PFHxS"  # the sum still comes first, as before
    assert "PFOS on its own" in quantities
    assert "PFHxS on its own" in quantities
    pfos = next(s for s in nemp.screens if s.compared_quantity == "PFOS on its own")
    assert (pfos.compared_value, pfos.limit, pfos.status) == ("0.062", "0.07", "not above")
    assert "footnote a" in pfos.explanation
    current = next(r for r in result.rules if r.rule == "current")
    assert not any("on its own" in s.compared_quantity for s in current.screens)


def test_a_member_alone_is_screened_when_the_other_member_is_missing() -> None:
    ds = default_dataset()
    rule = ds.rules["nemp-3.0"]
    limit = next(limit for limit in rule.limits if limit.applies_to == "sum")
    round_ = ds.rounds("MB2")[0]
    pfos = dataclasses.replace(round_.results["PFOS"], value=Decimal("0.08"))
    only_pfos = dataclasses.replace(round_, results={"PFOS": pfos})
    assert screen_limit(limit, only_pfos).status == "not analysed"
    alone = screen_member(limit, "PFOS", only_pfos)
    assert (alone.status, alone.above, alone.alone) == ("above", True, "PFOS")
    statuses = {s.alone: s.status for s in screens_for_rule(rule, only_pfos)}
    assert statuses["PFOS"] == "above"
    assert statuses["PFHxS"] == "not analysed"


def test_screen_member_refuses_a_non_member() -> None:
    ds = default_dataset()
    limit = next(limit for limit in ds.rules["nemp-3.0"].limits if limit.applies_to == "sum")
    with pytest.raises(ValueError, match="not a member"):
        screen_member(limit, "PFOA", ds.rounds("MB2")[0])


def test_tidy_water_sum_criteria_screen_each_member_too() -> None:
    sums = [c for c in water_criteria() if c.is_sum]
    assert sums
    assert all(c.each_member_too for c in sums)


def test_fill_numbers_refusal_for_a_member_limit_is_truthful() -> None:
    from evidenceline.drafting import fill_placeholders

    with pytest.raises(EvidencelineError) as caught:
        fill_placeholders("The value is {limit|PFHxS|nemp-3.0}.", "MB2")
    message = str(caught.value)
    assert "applies to PFHxS on its own too" in message
    assert "{limit|PFOS+PFHxS|nemp-3.0}" in message
    assert "no value for" not in message


# ---------- 2. show_redactions only promises what the loaded identifiers can do ----------


def test_redaction_explanation_without_identifiers_says_names_are_not_redacted() -> None:
    summary = Redactor(RedactionConfig((), "built-in patterns only"), None).summary()
    assert "NOT redacted" in summary.explanation
    assert "[CLIENT-1]" not in summary.explanation
    assert "on this computer" not in summary.explanation
    assert not any(d in summary.explanation for d in DASHES)


def test_redaction_explanation_with_identifiers_mentions_client_placeholders() -> None:
    config = RedactionConfig((redact.Identity("CLIENT", ("Example Holdings",)),), "Identifier file loaded.")
    summary = Redactor(config, None).summary()
    assert "[CLIENT-1]" in summary.explanation
    assert "NOT redacted" not in summary.explanation


# ---------- 5 and 4. The pre-publish scan: bearer tokens, token settings, the published build settings ----------


def _scan_tree(tmp_path: Path, files: dict[str, str]) -> list[str]:
    scanner = _load_script("prepublish_check")
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return [str(hit) for hit in scanner.scan(tmp_path, scanner.files_to_publish(tmp_path))]


def _token() -> str:
    return "".join(("Qx7vB2mK9p", "L4nR8tY1wE", "5zA3cD6fG0"))


def test_scan_catches_bearer_and_token_settings(tmp_path: Path) -> None:
    hits = _scan_tree(
        tmp_path,
        {
            "DEPLOY.md": f"curl -H 'Authorization: Bearer {_token()}' https://api.example\n"
            f"token: {_token()}\n"
            f"CLOUDFLARE_API_TOKEN={_token()}\n",
        },
    )
    assert [h.split(": [")[0] for h in hits] == ["DEPLOY.md:1", "DEPLOY.md:2", "DEPLOY.md:3"]
    assert all("[secret]" in h for h in hits)
    assert not any(_token() in h for h in hits)


def test_scan_leaves_placeholders_alone(tmp_path: Path) -> None:
    hits = _scan_tree(
        tmp_path,
        {
            "DEPLOY.md": "curl -H 'Authorization: Bearer <token>' https://api.example\n"
            'curl -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" https://api.example\n'
            "token: YOUR_TOKEN_HERE_PLEASE_REPLACE\n"
            "The proxy sends the secret in a header; see the token budget of 25000 tokens.\n",
        },
    )
    assert hits == []


def test_scan_allows_public_vite_build_settings_only(tmp_path: Path) -> None:
    clean = "# Public build settings\nVITE_API_BASE=/\nVITE_MCP_URL=https://example.org/mcp\n"
    assert _scan_tree(tmp_path, {"web/.env.production": clean}) == []
    hits = _scan_tree(tmp_path, {"web/.env.production": clean + "API_ORIGIN=https://internal.example\n"})
    wanted = "web/.env.production:4: [env-file] only public VITE_ build settings belong in this published file"
    assert hits == [f"{wanted}, not 'API_ORIGIN'"]
    other = _scan_tree(tmp_path / "other", {"api/.env.production": "VITE_X=1\n"})
    assert other == ["api/.env.production: [env-file] a local environment file; commit a .env.example template instead"]


def test_the_real_production_build_settings_pass_the_scan() -> None:
    scanner = _load_script("prepublish_check")
    assert list(scanner.scan_file(REPO, PurePosixPath("web/.env.production"))) == []


# ---------- 6. Search: question-framing verbs do not outweigh the subject ----------


def test_framing_verbs_are_not_search_terms() -> None:
    assert {"involve", "involves", "entail"} <= STOPWORDS
    labels = [c.label for c in query_concepts("What does a preliminary site investigation involve?")]
    assert labels == ["preliminary site investigation"]
    assert "involvement" in [c.label for c in query_concepts("community involvement in remediation")]


# ---------- 7. The held-out set is never read in the golden format ----------


def test_build_accuracy_refuses_a_golden_format_heldout_file(tmp_path: Path) -> None:
    script = _load_script("build_accuracy")
    impostor = tmp_path / "guidance_heldout.json"
    shutil.copy(evaluate.GOLDEN_PATH, impostor)
    spec = next(s for s in script.SEARCH_SETS if s[0] == "held-out")
    with pytest.raises(ValueError, match="not in the held-out format"):
        script.search_set(spec[0], spec[1], spec[2], impostor, spec[4])


def test_load_golden_refuses_the_heldout_format() -> None:
    with pytest.raises(ValueError, match="not in the golden format"):
        evaluate.load_golden(evaluate.HELDOUT_PATH)


def test_evaluate_main_refuses_a_golden_format_heldout_with_a_plain_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    impostor = tmp_path / "guidance_heldout.json"
    shutil.copy(evaluate.GOLDEN_PATH, impostor)
    monkeypatch.setattr(evaluate, "HELDOUT_PATH", impostor)
    assert evaluate.main(["--heldout"]) == 2
    assert "not in the held-out format" in capsys.readouterr().err


# ---------- the verification summary after the footnote a correction ----------


def test_a_note_changed_after_verification_is_listed_with_the_wording_the_passes_checked() -> None:
    summary = verification.summary_from_files()
    note = next(n for n in summary["notes_not_confirmed"] if n["id"] == "water:nemp-3.0:PFOS+PFHxS")
    assert note["changed_since_verification"] is True
    assert "no separate value" in note["checked_note"]  # the old wording, which both passes judged a mismatch
    assert "footnote a" in note["file_note"]
    # The owner's assistant then removed "Total" from the arsenic note, so both notes now differ from what was judged.
    assert summary["counts"]["notes_changed_since_verification"] == 2
    arsenic = next(n for n in summary["notes_not_confirmed"] if n["id"] == "soil:S-As-A")
    assert arsenic["changed_since_verification"] is True
    assert "Total arsenic" in arsenic["checked_note"]
    assert "Total" not in arsenic["file_note"]


def test_accuracy_view_labels_the_changed_note() -> None:
    script = _load_script("build_accuracy")
    view = script.verification_view(verification.summary_from_files())
    note = next(n for n in view["notes"] if n["id"] == "water:nemp-3.0:PFOS+PFHxS")
    assert note["status"] == script.NOTE_RECHECKED  # reworded, then confirmed by both notes re-checks
    assert note["confirmed_now"] is True
    assert "no separate value" in note["checked_note"]
    assert not any(d in note["status"] for d in DASHES)
