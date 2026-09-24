"""Score ``search_guidelines`` against the golden question set (``evals/guidance_golden.json``).

Run from the repository root after fetching and indexing::

    .venv/Scripts/python -m evidenceline.guidance.evaluate                      # the golden set
    .venv/Scripts/python -m evidenceline.guidance.evaluate --heldout            # held-out set 1 (now tuning)
    .venv/Scripts/python -m evidenceline.guidance.evaluate --heldout2           # held-out set 2 (never tuned on)
    .venv/Scripts/python -m evidenceline.guidance.evaluate path/to/other.json   # any file in either format

Two file formats are read. The golden format is ``{"in_scope": [...], "out_of_scope": [...]}``. The held-out format
(``evals/guidance_heldout.json``, see ``evals/HELDOUT.md``) is a list of ``{id, question, expected, note}`` where
``expected`` is ``"not_covered"`` or a list of ``{doc_id, pdf_pages, sections?}``. Its web-page entries (no page
numbers) are scored on the document and section, and a subsection counts: "Health considerations > PFOS" is in
"Health considerations", as the held-out notes intend. The golden set keeps its exact section match. The second
held-out set (``evals/guidance_heldout2.json``, see ``evals/HELDOUT2.md``) has the same format; its id prefix gives
the group ('c' casual, 'p' practitioner, 'x' out of scope), so its casual questions are also scored on their own.

Metrics (each question is searched once with k = 8, the number of passages the live answer box reads):
    hit@1      share of in-scope questions whose first passage is on an expected page (or in an expected section)
    hit@5      share whose top five passages include one on an expected page
    hit@8      the same for the top eight
    recall@8   mean over in-scope questions of the share of expected pages (or sections) found in the top eight
    out-of-scope accuracy   share of out-of-scope questions answered "not covered"
Every rate is an exact fraction shown as a decimal; nothing here is a float. Each summary line is given for the
whole file and for each ``group`` in it: 'original' (the first 22 in-scope and 4 out-of-scope questions),
'practitioner' (p01 to p07), 'added' (out-of-scope o05 to o09) and 'casual' (c01 to c06 in scope, c07 to c12 out).
The summary also gives the question box's view: hit@8 counting the borderline passages the model is given for a
"not covered", and how many out-of-scope questions would reach the model.

Measured on 2026-09-24. The golden set was used to make every choice below, so expect lower rates on new questions.

    group (questions)              search                     hit@1   hit@5   hit@8   recall@8   not covered
    original (22 in, 4 out)        page chunks (before)       12      19      21      0.69       4/4
                                   passages (after)           16      21      22      0.83       4/4
    practitioner (7 in)            page chunks (before)       0       3       3       0.24
                                   passages (after)           5       6       7       0.69
    added (5 out)                  page chunks (before)                                          4/5
                                   passages (after)                                              5/5

"Before" is one chunk per page ranked by BM25 x coverage; "after" is passages of 150 to 300 words with heading path
and caption columns, heading and phrase bonuses, synonym triggers, one passage per page and the more-than-half
rule. What each part is worth, measured by switching it off in the final search (original group hit@1/hit@5/hit@8,
recall@8; the other groups did not change unless shown):

    final                                  16/21/22  0.83
    no heading bonus                       15/20/22  0.82
    no phrase bonus                        13/21/22  0.83
    heading and caption weight 1.0 (2.0)   16/21/22  0.83   (no measurable difference; 2.0 kept)
    two passages per page (one)            16/21/21  0.74   practitioner recall@8 0.67 (0.69)
    no more-than-half rule                 16/21/22  0.83   added out-of-scope 3/5 (5/5)
    coverage threshold 0.35 to 0.45        no change; 0.30 lets 2 of 9 out-of-scope questions through; 0.50
                                           and above lose in-scope passages (0.45 kept)

Casual questions (2026-09-24). Held-out set 1 was first scored at hit@1 6/24, hit@8 7/24: 13 of its 17 misses
were a wrong "not covered" on chatty wording. After that run it was used for tuning together with the golden set,
so it is no longer a held-out measure. The changes: words no document contains add no weight (they still count in
the more-than-half rule), question framing and conversation words are not searched, plain-language synonym groups,
one-word synonyms still form word pairs, and a borderline band whose closest passages go to the question box's
model. Measured at k = 8:

    set                                    hit@1   hit@5   hit@8   recall@8   not covered
    golden, first 38 questions (before)    21/29   27/29   29/29   0.80       9/9
                               (after)     21/29   28/29   29/29   0.81       9/9
    golden with group 'casual' (after)     26/35   34/35   35/35   0.79       15/15
    held-out set 1 (before)                6/24    7/24    7/24    0.28       6/6
                   (after)                 17/24   20/24   20/24   0.83       6/6

Question box, counting the borderline passages the model is given: golden 35/35 with an expected page and 1 of 15
out-of-scope questions reaching the model (o03, which the model must answer NOT_COVERED); held-out set 1 21/24 and
0 of 6. The unknown-word rule alone changes neither set once the synonyms and stopwords are in, but on eight chatty
questions with new filler words ('my boss reckons', 'for my uni assignment') it lifts 4 of 8 found to 7 of 8.

Held-out set 2 (``--heldout2``), written without seeing the search or any results and never tuned on, first run on
2026-09-24 after the changes above: hit@1 12/24, hit@5 12/24, hit@8 12/24, recall@8 0.46, out-of-scope 5/6; casual
group 4/12 at every k (recall@8 0.29), practitioner group 8/12 (0.62). This is the fair score. Tuning on it would
turn it into a tuning set too.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from evidenceline.guidance.models import GuidanceSearch, Passage
from evidenceline.guidance.scope import NUMERIC_NOTE
from evidenceline.guidance.search import search_guidelines

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals"
GOLDEN_PATH = EVALS_DIR / "guidance_golden.json"
HELDOUT_PATH = EVALS_DIR / "guidance_heldout.json"
HELDOUT2_PATH = EVALS_DIR / "guidance_heldout2.json"
HELDOUT_GROUP = "heldout"
HELDOUT2_GROUPS = {"c": "casual", "p": "practitioner", "x": "out of scope"}
"""Held-out set 2: the group of each question, from the letter its id starts with."""
TOP_K = 5
LIVE_K = 8
"""The live answer box reads eight passages."""
DEFAULT_GROUP = "original"


@dataclass(frozen=True, slots=True)
class Expected:
    doc: str
    pdf_pages: frozenset[int]
    sections: frozenset[str]
    subsections_count: bool = False
    """True: a passage in a subsection ("A > B") also counts for section "A"."""

    def matches(self, passage: Passage) -> bool:
        return bool(self.targets_hit(passage))

    def targets(self) -> set[tuple[str, str]]:
        return {(self.doc, f"p{p}") for p in self.pdf_pages} | {(self.doc, f"s:{s}") for s in self.sections}

    def targets_hit(self, passage: Passage) -> set[tuple[str, str]]:
        if passage.document_id != self.doc:
            return set()
        hit: set[tuple[str, str]] = set()
        if passage.pdf_page is not None and passage.pdf_page in self.pdf_pages:
            hit.add((self.doc, f"p{passage.pdf_page}"))
        if passage.section is not None:
            for section in self.sections:
                inside = self.subsections_count and passage.section.startswith(f"{section} > ")
                if passage.section == section or inside:
                    hit.add((self.doc, f"s:{section}"))
        return hit


@dataclass(frozen=True, slots=True)
class GoldenQuestion:
    id: str
    question: str
    expected: tuple[Expected, ...]
    expects_numeric_note: bool
    group: str = DEFAULT_GROUP


@dataclass(frozen=True, slots=True)
class OutOfScopeQuestion:
    id: str
    question: str
    group: str = DEFAULT_GROUP


@dataclass(frozen=True, slots=True)
class Golden:
    in_scope: tuple[GoldenQuestion, ...]
    out_of_scope: tuple[OutOfScopeQuestion, ...]


@dataclass(frozen=True, slots=True)
class QuestionResult:
    """One question's outcome, for reports that list the misses."""

    id: str
    group: str
    question: str
    in_scope: bool
    status: str
    first_rank: int | None = None
    """In scope: the rank of the first passage on an expected page, or None for a miss."""
    recall8: Decimal | None = None
    correct: bool | None = None
    """Out of scope: whether the search said "not covered"."""
    top: tuple[str, ...] = ()
    """Where the first three passages came from, for example 'nemp-3.0 PDF 57'."""


@dataclass
class Tally:
    """Counts for one set of questions."""

    in_scope: int = 0
    hit1: int = 0
    hit5: int = 0
    hit8: int = 0
    recall8: Decimal = Decimal(0)
    """Sum of per-question recall; divide by ``in_scope`` for the mean."""
    out_of_scope: int = 0
    oos_correct: int = 0

    def summary(self) -> str:
        parts: list[str] = []
        if self.in_scope:
            n = self.in_scope
            recall = (self.recall8 / n).quantize(Decimal("0.01"))
            parts.append(
                f"hit@1 {self.hit1}/{n} ({EvalReport.rate(self.hit1, n)}), "
                f"hit@5 {self.hit5}/{n} ({EvalReport.rate(self.hit5, n)}), "
                f"hit@8 {self.hit8}/{n} ({EvalReport.rate(self.hit8, n)}), recall@8 {recall}"
            )
        if self.out_of_scope:
            parts.append(
                f"out-of-scope {self.oos_correct}/{self.out_of_scope} "
                f"({EvalReport.rate(self.oos_correct, self.out_of_scope)})"
            )
        return ", ".join(parts)


@dataclass
class EvalReport:
    hit1: int = 0
    hit5: int = 0
    hit8: int = 0
    recall8: Decimal = Decimal(0)
    in_scope: int = 0
    oos_correct: int = 0
    out_of_scope: int = 0
    groups: dict[str, Tally] = field(default_factory=dict[str, Tally])
    missing_numeric_note: list[str] = field(default_factory=list[str])
    lines: list[str] = field(default_factory=list[str])
    questions: list[QuestionResult] = field(default_factory=list[QuestionResult])
    box_hit8: int = 0
    """In-scope questions for which the question box gives the model a passage on an expected page: in the top eight
    passages, or, for a "not covered" that carries borderline passages, among those."""
    oos_to_model: int = 0
    """Out-of-scope questions that would reach the question box's model: passages found, or borderline passages."""

    @staticmethod
    def rate(part: int, whole: int) -> Decimal:
        return (Decimal(part) / Decimal(whole)).quantize(Decimal("0.01")) if whole else Decimal(0)

    @property
    def mean_recall8(self) -> Decimal:
        return (self.recall8 / self.in_scope).quantize(Decimal("0.01")) if self.in_scope else Decimal(0)

    def summary(self) -> str:
        overall = Tally(
            self.in_scope, self.hit1, self.hit5, self.hit8, self.recall8, self.out_of_scope, self.oos_correct
        )
        lines = [f"all: {overall.summary()}"]
        if len(self.groups) > 1:
            lines.extend(f"{name}: {tally.summary()}" for name, tally in sorted(self.groups.items()))
        box: list[str] = []
        if self.in_scope:
            box.append(f"hit@8 counting borderline passages {self.box_hit8}/{self.in_scope}")
        if self.out_of_scope:
            box.append(f"out-of-scope questions that reach the model {self.oos_to_model}/{self.out_of_scope}")
        if box:
            lines.append(f"question box: {', '.join(box)}")
        return "\n".join(lines)


def load_questions(path: Path) -> Golden:
    """Either file format: a JSON list is the held-out format, an object the golden format. Only for a file named on
    the command line; the held-out set itself is always read with ``load_heldout``, which accepts nothing else."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return _heldout(cast(list[dict[str, Any]], data))
    return load_golden(path)


def load_heldout(path: Path = HELDOUT_PATH) -> Golden:
    """The held-out set. Refuses a file in the golden format, so a tuning set can never be scored as held out."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(
            f"{path.name} is not in the held-out format (a JSON list of questions); it looks like the golden "
            "(tuning) format. The held-out score is only reported for questions the search was never tuned on."
        )
    return _heldout(cast(list[dict[str, Any]], data))


def load_heldout2(path: Path = HELDOUT2_PATH) -> Golden:
    """Held-out set 2, in the held-out format, with each question in the group its id prefix names. An id with no
    known prefix is an error, and so is an out-of-scope id outside group 'x' or an answerable one inside it."""
    loaded = load_heldout(path)

    def group(qid: str, in_scope: bool) -> str:
        name = HELDOUT2_GROUPS.get(qid[:1]) if qid[1:].isdigit() else None
        if name is None:
            raise ValueError(f"{qid}: a held-out set 2 id starts with one of {', '.join(HELDOUT2_GROUPS)}")
        if (name == HELDOUT2_GROUPS["x"]) == in_scope:
            raise ValueError(f"{qid}: only 'x' questions are expected to be not covered")
        return name

    return Golden(
        tuple(dataclasses.replace(q, group=group(q.id, True)) for q in loaded.in_scope),
        tuple(dataclasses.replace(q, group=group(q.id, False)) for q in loaded.out_of_scope),
    )


def _heldout(items: list[dict[str, Any]]) -> Golden:
    in_scope: list[GoldenQuestion] = []
    out: list[OutOfScopeQuestion] = []
    for raw in items:
        qid, question, expected = str(raw["id"]), str(raw["question"]), raw["expected"]
        if expected == "not_covered":
            out.append(OutOfScopeQuestion(qid, question, HELDOUT_GROUP))
            continue
        targets = tuple(
            Expected(
                doc=str(e["doc_id"]),
                pdf_pages=frozenset(cast(list[int], e.get("pdf_pages", []))),
                sections=frozenset(str(s) for s in cast(list[object], e.get("sections", []))),
                subsections_count=True,
            )
            for e in cast(list[dict[str, Any]], expected)
        )
        if not targets or not all(t.pdf_pages or t.sections for t in targets):
            raise ValueError(f"{qid}: every expected document needs pdf_pages or sections")
        in_scope.append(GoldenQuestion(qid, question, targets, False, HELDOUT_GROUP))
    return Golden(tuple(in_scope), tuple(out))


def load_golden(path: Path = GOLDEN_PATH) -> Golden:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} is not in the golden format (an object with in_scope and out_of_scope).")
    data = cast(dict[str, Any], loaded)
    in_scope: list[GoldenQuestion] = []
    for raw in cast(list[dict[str, Any]], data["in_scope"]):
        expected = tuple(
            Expected(
                doc=str(e["doc"]),
                pdf_pages=frozenset(cast(list[int], e.get("pdf_pages", []))),
                sections=frozenset(str(s) for s in cast(list[object], e.get("sections", []))),
            )
            for e in cast(list[dict[str, Any]], raw["expected"])
        )
        in_scope.append(
            GoldenQuestion(
                str(raw["id"]),
                str(raw["question"]),
                expected,
                bool(raw.get("expects_numeric_note")),
                str(raw.get("group", DEFAULT_GROUP)),
            )
        )
    out = tuple(
        OutOfScopeQuestion(str(r["id"]), str(r["question"]), str(r.get("group", DEFAULT_GROUP)))
        for r in cast(list[dict[str, Any]], data["out_of_scope"])
    )
    return Golden(tuple(in_scope), out)


def recall(expected: Sequence[Expected], passages: Sequence[Passage]) -> Decimal:
    """Share of the expected pages (and sections) that at least one of ``passages`` is on."""
    targets = set[tuple[str, str]]().union(*(e.targets() for e in expected))
    found = {t for p in passages for e in expected for t in e.targets_hit(p)}
    return Decimal(len(found)) / Decimal(len(targets)) if targets else Decimal(0)


def evaluate(golden: Golden, search: Callable[[str, int], GuidanceSearch] = search_guidelines) -> EvalReport:
    report = EvalReport(in_scope=len(golden.in_scope), out_of_scope=len(golden.out_of_scope))
    for item in golden.in_scope:
        result = search(item.question, LIVE_K)
        passages = result.passages[:LIVE_K]
        ranks = [p.rank for p in passages if any(e.matches(p) for e in item.expected)]
        first = min(ranks) if ranks else None
        share = recall(item.expected, passages)
        tally = report.groups.setdefault(item.group, Tally())
        for target in (report, tally):
            target.hit1 += first == 1
            target.hit5 += first is not None and first <= TOP_K
            target.hit8 += first is not None
            target.recall8 += share
        tally.in_scope += 1
        closest = list(result.closest_passages)[:LIVE_K] if result.status == "not covered" else []
        near_hit = any(e.matches(p) for p in closest for e in item.expected)
        report.box_hit8 += first is not None or near_hit
        if item.expects_numeric_note and NUMERIC_NOTE not in result.notes:
            report.missing_numeric_note.append(item.id)
        top = tuple(f"{p.document_id} PDF {p.pdf_page or p.section}" for p in passages[:3])
        label = f"hit@{first}" if first else "MISS"
        report.lines.append(
            f"{item.id} {label:7} recall {share.quantize(Decimal('0.01'))} {result.status}: {', '.join(top)}"
            + (" (an expected page is among the borderline passages)" if near_hit else "")
        )
        report.questions.append(
            QuestionResult(item.id, item.group, item.question, True, result.status, first, share, top=top)
        )
    for item in golden.out_of_scope:
        result = search(item.question, LIVE_K)
        correct = result.status == "not covered"
        report.oos_correct += correct
        near = correct and bool(result.closest_passages)
        report.oos_to_model += not correct or near
        tally = report.groups.setdefault(item.group, Tally())
        tally.out_of_scope += 1
        tally.oos_correct += correct
        report.lines.append(
            f"{item.id} {'ok' if correct else 'WRONG':7} {result.status}"
            + (" (with borderline passages, so the question box asks the model)" if near else "")
        )
        top = tuple(f"{p.document_id} PDF {p.pdf_page or p.section}" for p in result.passages[:3])
        report.questions.append(
            QuestionResult(item.id, item.group, item.question, False, result.status, correct=correct, top=top)
        )
    return report


def main(argv: Sequence[str] = ()) -> int:
    args = list(argv)
    loader: Callable[[Path], Golden]
    if args == ["--heldout"]:
        path, loader = HELDOUT_PATH, load_heldout
    elif args == ["--heldout2"]:
        path, loader = HELDOUT2_PATH, load_heldout2
    elif len(args) == 1 and not args[0].startswith("-"):
        path, loader = Path(args[0]), load_questions
    elif not args:
        path, loader = GOLDEN_PATH, load_golden
    else:
        print(
            "usage: python -m evidenceline.guidance.evaluate [--heldout | --heldout2 | path/to/questions.json]",
            file=sys.stderr,
        )
        return 2
    try:
        questions = loader(path)
    except ValueError as exc:
        print(f"evaluate: {exc}", file=sys.stderr)
        return 2
    report = evaluate(questions)
    print("\n".join(report.lines))
    print(report.summary())
    if report.missing_numeric_note:
        print(f"numeric note missing for: {', '.join(report.missing_numeric_note)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
