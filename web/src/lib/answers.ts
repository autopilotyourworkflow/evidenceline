// Answers for the "Try it yourself" box: prepared answers (public/data/answers.json) and live ones (POST /api/ask).
// Both are the answering pipeline's AnswerResult (src/evidenceline/answer/models.py). The shape is read
// defensively: a field that is missing or of the wrong type is left out, never guessed, and a result that cannot be
// read at all is treated as unusable.

import { isRecord, isString } from './data';

export type Citation = {
  readonly n: string;
  /** Whether the answer cites this passage; null when the result does not say. */
  readonly cited: boolean | null;
  readonly document: string;
  readonly edition: string;
  readonly printedPage: string;
  readonly pdfPage: string;
  /** One line a person can follow, such as "p. 29 (PDF p. 34), 9.1 Introduction". */
  readonly location: string;
  readonly section: string;
  readonly excerpt: string;
  /** The licence notice that goes with the excerpt. */
  readonly notice: string;
  /** An http(s) link to the page, or null when there is none (never rendered as a dead link). */
  readonly link: string | null;
};

export type GuidelineValue = { readonly marker: string; readonly text: string; readonly source: string; readonly note: string };

export type Check = { readonly name: string; readonly passed: boolean; readonly detail: string };

/**
 * answered: a checked answer with sources. passages-only: no answer shown, only the passages. not-covered: the
 * guidance does not cover it. guard-rail: a verdict it will not give. about: a greeting or a question about
 * Evidenceline itself, with a fixed reply. paused: live answers are paused. error: failed.
 */
export type AnswerKind = 'answered' | 'passages-only' | 'not-covered' | 'guard-rail' | 'about' | 'paused' | 'error';

export type AnswerView = {
  readonly kind: AnswerKind;
  readonly status: string;
  readonly answer: string;
  readonly explanation: string;
  readonly citations: readonly Citation[];
  readonly values: readonly GuidelineValue[];
  readonly notes: readonly string[];
  readonly checks: readonly Check[];
  readonly checkSummary: string;
  readonly redactions: number;
  readonly model: string | null;
  /** A not-covered result's proposed spelling ("Did you mean ..."), asked only if the visitor picks it; or ''. */
  readonly didYouMean: string;
  /** Questions to try instead (a not-covered result, or the reply about Evidenceline itself), closest first. */
  readonly suggestions: readonly string[];
};

export type Prepared = { readonly date: string | null; readonly model: string | null; readonly label: string | null };

/** `chip`: offered as a suggested question; false for an entry from answers.json's lookup_only list. */
export type PreparedAnswer = { readonly question: string; readonly view: AnswerView; readonly prepared: Prepared; readonly chip: boolean };

const text = (v: unknown): string => (isString(v) ? v.trim() : typeof v === 'number' && Number.isFinite(v) ? String(v) : '');
const first = (r: Record<string, unknown>, keys: readonly string[]): string => {
  for (const key of keys) {
    const value = text(r[key]);
    if (value !== '') return value;
  }
  return '';
};
const texts = (v: unknown): string[] => (Array.isArray(v) ? v : isString(v) ? [v] : []).map(text).filter((t) => t !== '');

/**
 * The site never shows an em or en dash. Pipeline text is tidied for display: a dash between numbers becomes "to",
 * any other becomes a comma. This covers every field the page shows except excerpts quoted from documents, which
 * are left as the search returned them.
 */
export function withoutDashes(value: string): string {
  return value
    .replace(/(\d)\s*[\u2013\u2014]\s*(\d)/g, '$1 to $2')
    .replace(/\s*[\u2013\u2014]\s*/g, ', ')
    .replace(/,\s*,/g, ',');
}

const safeLink = (value: string): string | null => {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
};

function readCitations(v: unknown): Citation[] {
  if (!Array.isArray(v)) return [];
  return v.filter(isRecord).map((c, k) => ({
    n: first(c, ['number', 'n', 'rank']) || String(k + 1),
    cited: typeof c.cited === 'boolean' ? c.cited : null,
    document: withoutDashes(first(c, ['document', 'document_title', 'title'])) || 'Source document',
    edition: withoutDashes(first(c, ['edition'])),
    printedPage: first(c, ['printed_page']),
    pdfPage: first(c, ['pdf_page']),
    location: withoutDashes(first(c, ['location'])),
    section: withoutDashes(first(c, ['section'])),
    excerpt: first(c, ['excerpt']),
    notice: withoutDashes(first(c, ['notice'])),
    link: safeLink(first(c, ['link', 'url', 'official_url'])),
  }));
}

function readValues(v: unknown): GuidelineValue[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((g): GuidelineValue[] => {
    if (isString(g)) return g.trim() === '' ? [] : [{ marker: '', text: withoutDashes(g.trim()), source: '', note: '' }];
    if (!isRecord(g)) return [];
    const what = first(g, ['compared_quantity', 'label', 'applies_to', 'analyte', 'key']);
    const rule = first(g, ['rule_name', 'rule']);
    const value = first(g, ['value']);
    const shown = g.available === false || value === '' ? 'no value in this rule' : `${value} ${first(g, ['unit'])}`.trim();
    const page = [first(g, ['page_basis']), first(g, ['page'])].filter((x) => x !== '').join(' ');
    const where = [first(g, ['source_document', 'document']), first(g, ['table']), page].filter((x) => x !== '').join(', ');
    return [
      {
        marker: first(g, ['marker']),
        text: withoutDashes([what, shown].filter((x) => x !== '').join(': ') + (rule === '' ? '' : ` (${rule})`)),
        source: withoutDashes(where),
        note: withoutDashes(first(g, ['note'])),
      },
    ];
  });
}

function readChecks(v: unknown): { checks: Check[]; summary: string } {
  if (!isRecord(v)) return { checks: [], summary: '' };
  const checks = Array.isArray(v.checks)
    ? v.checks.filter(isRecord).flatMap((c) => {
        const name = first(c, ['name']);
        return name === '' || typeof c.passed !== 'boolean' ? [] : [{ name: withoutDashes(name), passed: c.passed, detail: withoutDashes(first(c, ['detail'])) }];
      })
    : [];
  return { checks, summary: withoutDashes(first(v, ['summary'])) };
}

function kindOf(status: string): AnswerKind {
  const s = status.toLowerCase().replace(/[_-]+/g, ' ');
  if (s.trim() === 'about') return 'about';
  if (/passages/.test(s)) return 'passages-only';
  if (/not covered|out of scope|no answer/.test(s)) return 'not-covered';
  if (/guard|declin|refus|verdict/.test(s)) return 'guard-rail';
  if (/paused/.test(s)) return 'paused';
  if (/error|fail/.test(s)) return 'error';
  return 'answered';
}

/** One answer result, prepared or live. Null when it has no recognisable status and no text at all. */
export function readAnswer(raw: unknown): AnswerView | null {
  if (!isRecord(raw)) return null;
  const status = first(raw, ['status', 'kind']);
  const answer = withoutDashes(first(raw, ['answer', 'text']));
  const explanation = withoutDashes(first(raw, ['explanation', 'message']));
  if (status === '' && answer === '' && explanation === '') return null;
  const { checks, summary } = readChecks(raw.verification);
  const redactions = typeof raw.question_redactions === 'number' && Number.isInteger(raw.question_redactions) ? raw.question_redactions : 0;
  return {
    kind: kindOf(status),
    status,
    answer,
    explanation,
    citations: readCitations(raw.citations),
    values: readValues(raw.guideline_values),
    notes: texts(raw.notes).map(withoutDashes),
    checks,
    checkSummary: summary,
    redactions,
    model: first(raw, ['model']) || null,
    didYouMean: withoutDashes(first(raw, ['did_you_mean'])),
    suggestions: texts(raw.suggestions).map(withoutDashes).slice(0, 3),
  };
}

function readPrepared(v: unknown, fallback: Prepared, model: string | null = null): Prepared {
  if (!isRecord(v)) return model === null ? fallback : { ...fallback, model };
  return {
    date: first(v, ['prepared_on', 'date']) || fallback.date,
    model: first(v, ['model']) || model || fallback.model,
    label: withoutDashes(first(v, ['label'])) || fallback.label,
  };
}

const NO_PREPARED: Prepared = { date: null, model: null, label: null };

/**
 * answers.json: {prepared_on, model, label, answers: [{question, prepared_on, label, result}], lookup_only: [...]} as
 * written by scripts/precompute_answers.py. Also read: a bare list, and date and model under a "prepared" object.
 * lookup_only holds more entries of the same shape (the questions the answers suggest): they are answered when asked,
 * but not offered as suggested questions.
 */
export function readAnswersFile(raw: unknown): PreparedAnswer[] {
  const list = Array.isArray(raw) ? raw : isRecord(raw) ? (raw.answers ?? raw.questions ?? raw.items) : undefined;
  if (!Array.isArray(list)) return [];
  const top = isRecord(raw) ? readPrepared(isRecord(raw.prepared) ? raw.prepared : raw, NO_PREPARED) : NO_PREPARED;
  // The top-level label describes the whole file; each answer has its own.
  const shared: Prepared = { ...top, label: null };
  const read = (entries: unknown[], chip: boolean): PreparedAnswer[] =>
    entries.filter(isRecord).flatMap((entry) => {
      const question = first(entry, ['question']);
      const view = readAnswer(entry.result ?? entry);
      if (question === '' || view === null) return [];
      return [{ question, view, prepared: readPrepared(isRecord(entry.prepared) ? entry.prepared : entry, shared, view.model), chip }];
    });
  const lookups = isRecord(raw) && Array.isArray(raw.lookup_only) ? raw.lookup_only : [];
  return [...read(list, true), ...read(lookups, false)];
}

/** The date and model the whole file was prepared with, for the small print under the suggested questions. */
export function readAnswersFileMeta(raw: unknown): Prepared {
  if (!isRecord(raw)) return NO_PREPARED;
  return readPrepared(isRecord(raw.prepared) ? raw.prepared : raw, NO_PREPARED);
}

export const isAnswersFile = (v: unknown): v is unknown => readAnswersFile(v).length > 0;

export type LiveOutcome =
  | { readonly kind: 'answer'; readonly view: AnswerView }
  | { readonly kind: 'rate-limited'; readonly retryAfter: number | null; readonly daily: boolean }
  | { readonly kind: 'paused'; readonly message: string }
  | { readonly kind: 'error'; readonly message: string };

/** How long the page waits for a live answer. The loading line (TRY.loading) promises "up to a minute". */
const TIMEOUT_MS = 60_000;

/** Seconds from a Retry-After header or a retry_after field, when it is a whole number. */
function seconds(v: unknown): number | null {
  const n = typeof v === 'number' ? v : isString(v) && /^\d+$/.test(v.trim()) ? Number.parseInt(v, 10) : Number.NaN;
  return Number.isInteger(n) && n >= 0 ? n : null;
}

/**
 * Asks the live service. Never throws: every failure comes back as an outcome with a message for the reader.
 * `turnstileToken` is the "not a robot" check's token, sent as turnstile_token when the page has one.
 */
export async function askLive(apiBase: string, question: string, signal: AbortSignal, turnstileToken: string | null = null): Promise<LiveOutcome> {
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), TIMEOUT_MS);
  const onAbort = () => timeout.abort();
  signal.addEventListener('abort', onAbort, { once: true });
  try {
    const response = await fetch(`${apiBase}/api/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(turnstileToken === null ? { question } : { question, turnstile_token: turnstileToken }),
      signal: timeout.signal,
    });
    const body: unknown = await response.json().catch(() => null);
    const said = isRecord(body) ? withoutDashes(first(body, ['error', 'detail', 'message'])) : '';
    if (response.status === 429) {
      const retryAfter = seconds(response.headers.get('Retry-After')) ?? (isRecord(body) ? seconds(body.retry_after) : null);
      // The service names the daily limit (its windows roll, so that wait can be short too); an hourly limit never
      // asks for more than an hour, so a longer wait is the daily one.
      return { kind: 'rate-limited', retryAfter, daily: /per day/i.test(said) || (retryAfter ?? 0) > 3600 };
    }
    if (response.status === 503) return { kind: 'paused', message: said };
    // The service's own reason when it gives a short one, never the status code, which means nothing to a reader.
    if (!response.ok) {
      if (said === '' || said.length > 200) return { kind: 'error', message: 'The question could not be answered.' };
      return { kind: 'error', message: /[.!?]$/.test(said) ? said : `The question could not be answered: ${said}.` };
    }
    const view = readAnswer(isRecord(body) && isRecord(body.result) ? body.result : body);
    if (view === null) return { kind: 'error', message: 'The answer came back in a form this page cannot show.' };
    return { kind: 'answer', view };
  } catch {
    if (signal.aborted) return { kind: 'error', message: 'Cancelled.' };
    if (timeout.signal.aborted) return { kind: 'error', message: 'The live service took too long to answer.' };
    return { kind: 'error', message: 'The live service could not be reached.' };
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', onAbort);
  }
}
