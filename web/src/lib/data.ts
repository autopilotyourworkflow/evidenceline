// Types and runtime checks for the JSON files in public/data, and a hook that loads one of them.
// The JSON is generated from the Python package's data (scripts/build-data.mjs and scripts/export_web_data.py).
// Concentrations stay strings. accuracy.json and answers.json have their own readers (accuracy.ts, answers.ts).

import { useEffect, useState } from 'react';

export type LabValue = {
  readonly analyte: string;
  readonly reported: string;
  readonly detected: boolean;
  readonly detection_limit: string;
  readonly unit: string;
  readonly row: number;
};

export type Round = {
  readonly round: number;
  readonly date: string;
  readonly sample_id: string;
  readonly lab_report_id: string;
  readonly matrix: string;
  readonly file: string;
  readonly path: string;
  readonly results: readonly LabValue[];
  readonly other_analytes: readonly LabValue[];
};

export type SampleSite = {
  readonly synthetic: true;
  readonly synthetic_notice: string;
  readonly site_id: string;
  readonly site_description: string;
  readonly well_id: string;
  readonly unit: string;
  readonly row_numbering: string;
  readonly rounds: readonly Round[];
};

export type Limit = {
  readonly key: string;
  readonly applies_to: 'sum' | 'single';
  readonly members: readonly string[];
  readonly value: string;
  readonly unit: string;
  readonly scenario: string;
  readonly note: string;
};

export type Rule = {
  readonly id: string;
  readonly name: string;
  readonly document: string;
  readonly table: string;
  readonly page: string;
  readonly page_basis: string;
  readonly wa_status: string;
  readonly links: readonly { readonly label: string; readonly url: string }[];
  readonly limits: readonly Limit[];
};

export type Sources = {
  readonly verified_on: string;
  readonly about: string;
  readonly choice_note: string;
  readonly rules: readonly Rule[];
};

export type SoilCriterion = {
  readonly id: string;
  readonly label: string;
  readonly members: readonly string[];
  readonly each_member_too: boolean;
  readonly value: string;
  readonly unit: string;
  readonly document: string;
  readonly table: string;
  readonly page: string;
  readonly note: string;
};

export type SoilCriteria = {
  readonly verified_on: string;
  readonly about: string;
  readonly scenario: string;
  readonly scenario_short: string;
  readonly used_for: string;
  readonly criteria: readonly SoilCriterion[];
};

/** tidy.json: a short tidy_lab_files result for FDS-01, written by scripts/export_web_data.py. */
export type TidyFile = {
  readonly file: string;
  readonly role: string;
  readonly header_row: number;
  readonly data_rows: number;
  readonly date_example: string;
};

export type ReviewItem = {
  readonly number: number;
  readonly check: string;
  readonly title: string;
  readonly found: string;
  readonly samples: readonly string[];
  readonly rule: string;
  readonly source: string;
  readonly scientist_decides: string;
  readonly evidence: readonly string[];
};

export type TidyCheck = {
  readonly check: string;
  readonly what_was_checked: string;
  readonly checked: number;
  readonly review_items: readonly number[];
};

export type TidyNotChecked = { readonly check: string; readonly what: string; readonly reason: string };

export type TidyCheckedItem = { readonly check: string; readonly what: string };

export type Tidy = {
  readonly site: string;
  readonly synthetic: string;
  readonly summary: string;
  readonly files: readonly TidyFile[];
  readonly samples: number;
  readonly results: number;
  readonly review_items: readonly ReviewItem[];
  readonly checks: readonly TidyCheck[];
  /** A count in the short export; the full list when the export includes it. */
  readonly checked_not_flagged: number | readonly TidyCheckedItem[];
  readonly not_checked: readonly TidyNotChecked[];
  readonly notes: readonly string[];
};

export type Guard<T> = (value: unknown) => value is T;

export const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
export const isString = (v: unknown): v is string => typeof v === 'string';
export const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v);
export const isArrayOf = <T>(v: unknown, item: (x: unknown) => x is T): v is T[] => Array.isArray(v) && v.every(item);
const hasStrings = (v: Record<string, unknown>, keys: readonly string[]): boolean => keys.every((k) => isString(v[k]));

/** A concentration written as text: digits, an optional decimal part, and an optional leading "<" (not detected). */
const DECIMAL_TEXT = /^<?\d+(\.\d+)?$/;

const isLabValue = (v: unknown): v is LabValue =>
  isRecord(v) &&
  hasStrings(v, ['analyte', 'reported', 'detection_limit', 'unit']) &&
  DECIMAL_TEXT.test(String(v.reported)) &&
  typeof v.detected === 'boolean' &&
  isInt(v.row);

const isRound = (v: unknown): v is Round =>
  isRecord(v) &&
  isInt(v.round) &&
  hasStrings(v, ['date', 'sample_id', 'lab_report_id', 'matrix', 'file', 'path']) &&
  isArrayOf(v.results, isLabValue) &&
  isArrayOf(v.other_analytes, isLabValue);

export const isSampleSite: Guard<SampleSite> = (v): v is SampleSite =>
  isRecord(v) &&
  v.synthetic === true &&
  hasStrings(v, ['synthetic_notice', 'site_id', 'site_description', 'well_id', 'unit', 'row_numbering']) &&
  isArrayOf(v.rounds, isRound);

const isLimit = (v: unknown): v is Limit =>
  isRecord(v) &&
  hasStrings(v, ['key', 'value', 'unit', 'scenario', 'note']) &&
  DECIMAL_TEXT.test(String(v.value)) &&
  (v.applies_to === 'sum' || v.applies_to === 'single') &&
  isArrayOf(v.members, isString);

const isLink = (v: unknown): v is { label: string; url: string } => isRecord(v) && hasStrings(v, ['label', 'url']);

const isRule = (v: unknown): v is Rule =>
  isRecord(v) &&
  hasStrings(v, ['id', 'name', 'document', 'table', 'page', 'page_basis', 'wa_status']) &&
  isArrayOf(v.links, isLink) &&
  isArrayOf(v.limits, isLimit);

export const isSources: Guard<Sources> = (v): v is Sources =>
  isRecord(v) && hasStrings(v, ['verified_on', 'about', 'choice_note']) && isArrayOf(v.rules, isRule);

const isSoilCriterion = (v: unknown): v is SoilCriterion =>
  isRecord(v) &&
  hasStrings(v, ['id', 'label', 'value', 'unit', 'document', 'table', 'page', 'note']) &&
  DECIMAL_TEXT.test(String(v.value)) &&
  !String(v.value).startsWith('<') &&
  typeof v.each_member_too === 'boolean' &&
  isArrayOf(v.members, isString);

export const isSoilCriteria: Guard<SoilCriteria> = (v): v is SoilCriteria =>
  isRecord(v) &&
  hasStrings(v, ['verified_on', 'about', 'scenario', 'scenario_short', 'used_for']) &&
  isArrayOf(v.criteria, isSoilCriterion);

const isTidyFile = (v: unknown): v is TidyFile =>
  isRecord(v) && hasStrings(v, ['file', 'role', 'date_example']) && isInt(v.header_row) && isInt(v.data_rows);

const isReviewItem = (v: unknown): v is ReviewItem =>
  isRecord(v) &&
  isInt(v.number) &&
  hasStrings(v, ['check', 'title', 'found', 'rule', 'source', 'scientist_decides']) &&
  isArrayOf(v.samples, isString) &&
  isArrayOf(v.evidence, isString);

const isTidyCheck = (v: unknown): v is TidyCheck =>
  isRecord(v) && hasStrings(v, ['check', 'what_was_checked']) && isInt(v.checked) && isArrayOf(v.review_items, isInt);

const isTidyNotChecked = (v: unknown): v is TidyNotChecked => isRecord(v) && hasStrings(v, ['check', 'what', 'reason']);

const isTidyCheckedItem = (v: unknown): v is TidyCheckedItem => isRecord(v) && hasStrings(v, ['check', 'what']);

export const isTidy: Guard<Tidy> = (v): v is Tidy =>
  isRecord(v) &&
  hasStrings(v, ['site', 'synthetic', 'summary']) &&
  isArrayOf(v.files, isTidyFile) &&
  isInt(v.samples) &&
  isInt(v.results) &&
  isArrayOf(v.review_items, isReviewItem) &&
  isArrayOf(v.checks, isTidyCheck) &&
  (isInt(v.checked_not_flagged) || isArrayOf(v.checked_not_flagged, isTidyCheckedItem)) &&
  isArrayOf(v.not_checked, isTidyNotChecked) &&
  isArrayOf(v.notes, isString);

export type Loaded<T> =
  | { readonly state: 'loading' }
  | { readonly state: 'error'; readonly message: string }
  | { readonly state: 'ready'; readonly data: T };

/** An error whose message is written for the reader, not for a developer. */
class LoadError extends Error {}

/** Fetches a JSON file from public/data and checks its shape before anything renders it. */
export function useJson<T>(file: string, guard: Guard<T>): Loaded<T> {
  const [loaded, setLoaded] = useState<Loaded<T>>({ state: 'loading' });
  useEffect(() => {
    const controller = new AbortController();
    fetch(`/data/${file}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new LoadError(`The file ${file} could not be loaded (HTTP ${response.status}).`);
        const body: unknown = await response.json().catch(() => {
          throw new LoadError(`The file ${file} is not valid JSON, so nothing from it is shown.`);
        });
        if (!guard(body)) throw new LoadError(`The file ${file} is not in the expected format, so nothing from it is shown.`);
        setLoaded({ state: 'ready', data: body });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setLoaded({ state: 'error', message: error instanceof LoadError ? error.message : `The file ${file} could not be loaded.` });
      });
    return () => controller.abort();
  }, [file, guard]);
  return loaded;
}
