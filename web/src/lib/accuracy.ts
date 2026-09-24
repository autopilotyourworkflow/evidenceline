// Reader for public/data/accuracy.json, written by the project's test run (see web/README.md for the shape).
// The file is read defensively: any part that is missing or in an unexpected shape is left out, never guessed.
// Counts are whole numbers; metric values are shown as the file writes them.

import { isArrayOf, isInt, isRecord, isString } from './data';

export type TestArea = {
  readonly name: string;
  readonly passed: number;
  readonly failed: number;
  readonly skipped: number;
  /** Names of the failed tests, when the file lists them. */
  readonly failures: readonly string[];
};

export type SearchMetric = {
  readonly name: string;
  /** As written in the file, for example "12 of 22" or "0.55". */
  readonly shown: string;
  /** Present when the metric is a count of hits out of a total. */
  readonly hits?: number;
  readonly total?: number;
};

/** A question the search got wrong: the right page was not in the results, or it answered an out-of-scope question. */
export type SearchMiss = { readonly id: string; readonly question: string; readonly result: string };

/** The same run's score with some questions left out (those close in wording to a tuning question). */
export type LeftOutScore = {
  /** Ids of the questions left out. */
  readonly leftOut: readonly string[];
  readonly questions: number | null;
  readonly metrics: readonly SearchMetric[];
};

export type SearchSet = {
  readonly id: string;
  /** The id of the set this one is a part of (a group of its questions, scored in the same run); else empty. */
  readonly partOf: string;
  readonly label: string;
  readonly note: string;
  readonly questions: number | null;
  readonly metrics: readonly SearchMetric[];
  readonly misses: readonly SearchMiss[];
  /** The score without the questions that turned out close to a tuning question, when the file gives it. */
  readonly withoutNearCopies: LeftOutScore | null;
};

export type Verdict = { readonly pass: string; readonly verdict: string; readonly agrees: boolean; readonly note: string };

export type CheckedValue = {
  readonly id: string;
  readonly label: string;
  readonly value: string;
  readonly unit: string;
  readonly source: string;
  readonly verdicts: readonly Verdict[];
  readonly note: string;
};

export type VerificationPass = { readonly id: string; readonly label: string; readonly method: string };

/** An explanatory note next to a value that at least one pass did not confirm (the value itself is listed separately). */
export type CheckedNote = {
  readonly id: string;
  readonly label: string;
  /** The note as the data file writes it. */
  readonly fileNote: string;
  /** The wording the passes judged, when the note was reworded after they checked it; else empty. */
  readonly checkedNote: string;
  /** Where the note stands, in plain words (not confirmed, or reworded and not re-checked). */
  readonly status: string;
  /** The source's own words, when a pass quoted them. */
  readonly sourceSays: string;
  readonly verdicts: readonly Verdict[];
  /** The later re-checks' verdicts on the wording the file holds now; empty when none looked at this note. */
  readonly rechecks: readonly Verdict[];
  /** True only when the file says every re-check confirmed the current wording. */
  readonly confirmedNow: boolean;
};

/** Something else a re-check judged: a related note, the server's wording, what a tool returns. */
export type RecheckFinding = {
  readonly id: string;
  readonly label: string;
  /** What the first two passes said about it, when it is a note they checked. */
  readonly context: string;
  readonly verdicts: readonly Verdict[];
  /** How it was fixed afterwards, when it was; the verdicts stay as the re-checks recorded them. */
  readonly resolution: string;
  /** Still needs a person: not fixed, and not confirmed by every re-check that looked at it. */
  readonly open: boolean;
};

export type AccuracyView = {
  readonly hasResults: boolean;
  readonly message: string;
  readonly lastRun: string | null;
  /** "commit abc1234" or "local build". */
  readonly build: string | null;
  readonly tests: readonly TestArea[];
  readonly search: readonly SearchSet[];
  readonly passes: readonly VerificationPass[];
  /** Later, independent re-checks of the notes reworded after the first two passes. */
  readonly rechecks: readonly VerificationPass[];
  readonly values: readonly CheckedValue[];
  readonly notes: readonly CheckedNote[];
  readonly findings: readonly RecheckFinding[];
};

/** Text as the file writes it. A number is only turned into text for display, never used in arithmetic. */
const str = (v: unknown): string => (isString(v) ? v : typeof v === 'number' && Number.isFinite(v) ? String(v) : '');
const firstString = (r: Record<string, unknown>, keys: readonly string[]): string => {
  for (const key of keys) {
    const value = str(r[key]);
    if (value !== '') return value;
  }
  return '';
};
const count = (v: unknown): number => (isInt(v) && v >= 0 ? v : 0);

/** Words a verification pass may use for "the value matches its source page". Anything else counts as a disagreement. */
const AGREE = /^(match(es|ed)?|pass(ed)?|ok|agree[sd]?|confirmed|verified|correct|true)$/i;

function readTests(raw: Record<string, unknown>): TestArea[] {
  const nested = isRecord(raw.tests) ? raw.tests.areas : undefined;
  const list = [raw.suites, raw.tests, raw.test_counts, raw.areas, nested].find((v) => Array.isArray(v) && v.length > 0);
  if (!Array.isArray(list)) return [];
  return list.filter(isRecord).flatMap((t) => {
    const name = firstString(t, ['name', 'area', 'suite', 'label']);
    if (name === '' || !isInt(t.passed)) return [];
    const failures = isArrayOf(t.failures, isString) ? t.failures : [];
    return [{ name, passed: count(t.passed), failed: count(t.failed), skipped: count(t.skipped), failures }];
  });
}

function readMetric(name: string, v: unknown): SearchMetric | null {
  if (!isRecord(v)) {
    const shown = str(v);
    return shown === '' ? null : { name, shown };
  }
  const hits = [v.hits, v.passed, v.correct].find(isInt);
  const total = [v.of, v.total, v.questions].find(isInt);
  if (hits !== undefined && total !== undefined) return { name, shown: `${hits} of ${total}`, hits, total };
  const shown = firstString(v, ['value', 'shown', 'rate']);
  return shown === '' ? null : { name, shown };
}

function readMetrics(v: unknown): SearchMetric[] {
  if (Array.isArray(v)) {
    return v.filter(isRecord).flatMap((m) => {
      const name = firstString(m, ['name', 'metric', 'label']);
      const metric = name === '' ? null : readMetric(name, m);
      return metric === null ? [] : [metric];
    });
  }
  if (isRecord(v)) {
    return Object.entries(v).flatMap(([name, value]) => {
      const metric = readMetric(name, value);
      return metric === null ? [] : [metric];
    });
  }
  return [];
}

/** Keys of a search set that describe the set rather than measure it. */
const SET_KEYS = new Set(['set', 'id', 'name', 'label', 'title', 'description', 'questions', 'size', 'count', 'note', 'misses', 'part_of', 'near_copies', 'without_near_copies']);

function readMisses(v: unknown): SearchMiss[] {
  if (!isArrayOf(v, isRecord)) return [];
  return v.flatMap((m) => {
    const question = firstString(m, ['question']);
    return question === '' ? [] : [{ id: firstString(m, ['id']), question, result: firstString(m, ['result', 'reason']) }];
  });
}

function readLeftOut(v: unknown): LeftOutScore | null {
  if (!isRecord(v) || !isArrayOf(v.left_out, isString) || v.left_out.length === 0) return null;
  const metrics = readMetrics(v.metrics);
  if (metrics.length === 0) return null;
  return { leftOut: v.left_out, questions: isInt(v.questions) ? v.questions : null, metrics };
}

function readSearch(raw: Record<string, unknown>): SearchSet[] {
  const source = raw.search ?? raw.search_metrics;
  const sets: [string, unknown][] = Array.isArray(source)
    ? source.filter(isRecord).map((s) => [firstString(s, ['set', 'id', 'name']), s])
    : isRecord(source)
      ? Object.entries(source)
      : [];
  return sets.flatMap(([id, s]) => {
    if (!isRecord(s) || id === '') return [];
    const metrics = readMetrics(s.metrics ?? Object.fromEntries(Object.entries(s).filter(([key]) => !SET_KEYS.has(key))));
    if (metrics.length === 0) return [];
    const questions = [s.questions, s.size, s.count].find(isInt) ?? null;
    return [
      {
        id,
        partOf: firstString(s, ['part_of']),
        label: firstString(s, ['label', 'title', 'description']) || id,
        note: firstString(s, ['note']),
        questions,
        metrics,
        misses: readMisses(s.misses),
        withoutNearCopies: readLeftOut(s.without_near_copies),
      },
    ];
  });
}

function readVerdicts(v: unknown): Verdict[] {
  const make = (pass: string, value: unknown): Verdict | null => {
    if (isString(value)) return { pass, verdict: value, agrees: AGREE.test(value.trim()), note: '' };
    if (typeof value === 'boolean') return { pass, verdict: value ? 'match' : 'mismatch', agrees: value, note: '' };
    if (isRecord(value)) {
      const verdict = firstString(value, ['verdict', 'result', 'status']);
      if (verdict === '') return null;
      return { pass, verdict, agrees: AGREE.test(verdict.trim()), note: firstString(value, ['note', 'reason', 'detail']) };
    }
    return null;
  };
  if (Array.isArray(v)) {
    return v.filter(isRecord).flatMap((x) => {
      const verdict = make(firstString(x, ['pass', 'id', 'name']), x);
      return verdict === null || verdict.pass === '' ? [] : [verdict];
    });
  }
  if (isRecord(v)) {
    return Object.entries(v).flatMap(([pass, value]) => {
      const verdict = make(pass, value);
      return verdict === null ? [] : [verdict];
    });
  }
  return [];
}

function readPasses(v: unknown): VerificationPass[] {
  if (!isArrayOf(v, isRecord)) return [];
  return v.flatMap((p) => {
    const id = firstString(p, ['id', 'name', 'pass']);
    return id === '' ? [] : [{ id, label: firstString(p, ['label', 'title']), method: firstString(p, ['method', 'description', 'summary']) }];
  });
}

function readFindings(v: unknown): RecheckFinding[] {
  if (!isArrayOf(v, isRecord)) return [];
  return v.flatMap((f) => {
    const id = firstString(f, ['id']);
    const verdicts = readVerdicts(f.verdicts);
    if (id === '' || verdicts.length === 0) return [];
    const resolution = firstString(f, ['resolution']);
    // Without an explicit flag, a finding is open when it is not fixed and a re-check that looked did not confirm it.
    const open = typeof f.open === 'boolean' ? f.open : resolution === '' && verdicts.some((d) => !d.agrees && d.verdict !== 'not checked');
    return [{ id, label: firstString(f, ['label', 'subject']) || id, context: firstString(f, ['context']), verdicts, resolution, open }];
  });
}

type VerificationParts = Pick<AccuracyView, 'passes' | 'rechecks' | 'values' | 'notes' | 'findings'>;

function readVerification(raw: Record<string, unknown>): VerificationParts {
  const ver = raw.verification ?? raw.guideline_verification;
  if (!isRecord(ver)) return { passes: [], rechecks: [], values: [], notes: [], findings: [] };
  const passes = readPasses(ver.passes);
  const values = isArrayOf(ver.values, isRecord)
    ? ver.values.flatMap((v) => {
        const id = firstString(v, ['id', 'value_id', 'key']);
        const verdicts = readVerdicts(v.verdicts ?? v.passes);
        if (id === '' || verdicts.length === 0) return [];
        return [
          {
            id,
            label: firstString(v, ['label', 'name']) || id,
            value: firstString(v, ['value']),
            unit: firstString(v, ['unit']),
            source: firstString(v, ['source', 'page', 'location']),
            verdicts,
            note: firstString(v, ['note']),
          },
        ];
      })
    : [];
  const notes = isArrayOf(ver.notes, isRecord)
    ? ver.notes.flatMap((n) => {
        const id = firstString(n, ['id']);
        const verdicts = readVerdicts(n.verdicts);
        if (id === '' || verdicts.length === 0) return [];
        return [
          {
            id,
            label: firstString(n, ['label']) || id,
            fileNote: firstString(n, ['file_note']),
            checkedNote: firstString(n, ['checked_note']),
            status: firstString(n, ['status']),
            sourceSays: firstString(n, ['source_says']),
            verdicts,
            rechecks: readVerdicts(n.rechecks),
            confirmedNow: n.confirmed_now === true,
          },
        ];
      })
    : [];
  return { passes, rechecks: readPasses(ver.rechecks), values, notes, findings: readFindings(ver.recheck_findings) };
}

/** Reads accuracy.json into what the page shows. Returns null when the file is not an object at all. */
export function readAccuracy(raw: unknown): AccuracyView | null {
  if (!isRecord(raw)) return null;
  const tests = readTests(raw);
  const search = readSearch(raw);
  const { passes, rechecks, values, notes, findings } = readVerification(raw);
  const commit = firstString(raw, ['commit']);
  const build = firstString(raw, ['build']) || (commit === '' ? '' : `commit ${commit}`);
  const hasResults = raw.status !== 'pending' && (tests.length > 0 || search.length > 0 || values.length > 0);
  return {
    hasResults,
    message: firstString(raw, ['message']) || 'Results will appear here when the automated tests run.',
    lastRun: firstString(raw, ['last_run', 'date', 'generated_on']) || null,
    build: build || null,
    tests,
    search,
    passes,
    rechecks,
    values,
    notes,
    findings,
  };
}

export const isAccuracyFile = (v: unknown): v is Record<string, unknown> => readAccuracy(v) !== null;

/** Plain names for the search metrics the evaluation reports. Unknown names are shown as written. */
export const METRIC_NAMES: Readonly<Record<string, string>> = {
  'hit@1': 'Right page ranked first',
  hit_at_1: 'Right page ranked first',
  'hit@5': 'Right page in the top five',
  hit_at_5: 'Right page in the top five',
  'hit@8': 'Right page in the top eight',
  'recall@8': 'Share of the right pages found in the top eight',
  'out-of-scope accuracy': 'Says "not covered" when it should',
  out_of_scope: 'Says "not covered" when it should',
  out_of_scope_accuracy: 'Says "not covered" when it should',
  mrr: 'Mean reciprocal rank',
};
