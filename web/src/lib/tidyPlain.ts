// Plain-English lines for the tidy review items, for readers who are not scientists.
// Every name and number in a line is read from the tool's own output (tidy.json); nothing here is typed in by hand.
// When an item's heading is not in the expected form, the line falls back to wording with no numbers in it.

import type { ReviewItem } from './data';

export type PlainLine = {
  /** A few words, for the landing preview. */
  readonly short: string;
  /** One sentence, for the full example page. */
  readonly line: string;
};

/**
 * Rounds a decimal written as text to a whole number, half up, using digits only (no floating point).
 * "61.8" gives "62", "61.4" gives "61", "7" gives "7". Anything else is returned unchanged.
 */
export function roundHalfUp(text: string): string {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(text);
  if (match === null) return text;
  const whole = match[1] ?? '0';
  const firstDecimal = (match[2] ?? '0').charAt(0);
  if (firstDecimal < '5') return String(BigInt(whole));
  return String(BigInt(whole) + 1n);
}

/** "ug/L" as the page shows it. */
const unit = (text: string): string => text.replace(/\bug\/L\b/g, 'µg/L');

type Rule = {
  readonly pattern: RegExp;
  readonly make: (m: readonly (string | undefined)[], item: ReviewItem) => PlainLine;
  readonly fallback: PlainLine;
};

const escapeRegExp = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * "soil" or "water" for a sample, read from the unit the tool's own text gives its result ("SB4-0.3 180 mg/kg" is
 * soil; a result per litre is water). Null when the text does not say, so no line ever guesses the kind of sample.
 */
export function sampleMatrix(sample: string, text: string): 'soil' | 'water' | null {
  if (sample === '') return null;
  const match = new RegExp(`(?:^|[\\s(:])${escapeRegExp(sample)} <?\\d+(?:\\.\\d+)? (mg/kg|[uµn]g/L|mg/L)\\b`).exec(text);
  if (match === null) return null;
  return match[1] === 'mg/kg' ? 'soil' : 'water';
}

const RULES: Readonly<Record<string, Rule>> = {
  'sample ids': {
    pattern: /^Sample id written two ways: (\S+) in the lab file, (\S+) on the chain of custody$/,
    make: ([, lab = '', paperwork = '']) => ({
      short: `Sample "${paperwork}" is written "${lab}" in the lab file`,
      line: `One sample name is written two ways: "${paperwork}" on the sample paperwork and "${lab}" in the lab file, so its results cannot be matched yet.`,
    }),
    fallback: {
      short: 'A sample name is written two ways',
      line: 'One sample name is written two ways in different files, so its results cannot be matched yet.',
    },
  },
  units: {
    pattern: /^(\S+) reported in (\S+); the other water results are in (\S+)$/,
    make: ([, sample = '', odd = '', usual = '']) => ({
      short: "One sample's results are in a different unit from the rest",
      line: `One sample (${sample}) was reported in ${unit(odd)} while the others are in ${unit(usual)}. Evidenceline converted it exactly and flags it for a person to confirm.`,
    }),
    fallback: {
      short: "One sample's results are in a different unit from the rest",
      line: "One sample's results are in a different unit from the rest. They were converted exactly and flagged for a person to confirm.",
    },
  },
  'field duplicates': {
    pattern: /^Field duplicate pair (\S+) and (\S+): (.+?) RPD (\d+(?:\.\d+)?)%, above (\d+(?:\.\d+)?)%$/,
    make: ([, first = '', , analyte = '', rpd = '', limit = ''], item) => {
      const matrix = sampleMatrix(first, item.found);
      return {
        short: `A repeat test of the same sample differs by ${roundHalfUp(rpd)}%`,
        line: `A repeat test of the same ${matrix === null ? '' : `${matrix} `}sample differs by ${roundHalfUp(rpd)}% for ${analyte.toLowerCase()}, more than the ${limit}% at which the cause should be looked into.`,
      };
    },
    fallback: {
      short: 'A repeat test of the same sample differs by more than expected',
      line: 'A repeat test of the same sample differs by more than expected, so the cause should be looked into.',
    },
  },
  'holding times': {
    pattern: /^(.+?) in (\S+) extracted after the holding time \((\d+) days; limit (\d+) days\)$/,
    make: ([, analyte = '', , days = '', limit = '']) => {
      const over = Number.parseInt(days, 10) - Number.parseInt(limit, 10);
      return {
        short: 'One sample was tested after its time limit',
        line: `One sample was tested ${over} ${over === 1 ? 'day' : 'days'} after its time limit (${analyte.toLowerCase()}: ${days} days after sampling, where the limit is ${limit}).`,
      };
    },
    fallback: {
      short: 'One sample was tested after its time limit',
      line: 'One sample was tested after the time limit for its test.',
    },
  },
  blanks: {
    pattern: /^(\S+) detected in (.+?) (\S+)(?:; linked to a marginal result at (\S+))?$/,
    make: ([, analyte = '', kind = '', , linked]) => {
      const rinsate = /rinsate|equipment/i.test(kind);
      const who = rinsate ? 'A cleaning-check sample' : 'A check sample';
      const what = rinsate ? `${who} (clean water poured over the sampling equipment)` : `${who} (${kind})`;
      const from = rinsate ? 'the equipment' : 'the sampling process';
      return {
        short: `${who} picked up ${analyte}`,
        line:
          linked === undefined || linked === ''
            ? `${what} picked up ${analyte}.`
            : `${what} picked up ${analyte}, so one nearby result (${linked}) may partly come from ${from}.`,
      };
    },
    fallback: {
      short: 'A cleaning-check sample picked something up',
      line: 'A cleaning-check sample picked up a chemical it should not contain.',
    },
  },
  'LOR against criteria': {
    pattern: /^(\S+) (\S+): LOR above (.+?) \((\d+(?:\.\d+)?) (\S+)\) under (.+); not confirmed$/,
    make: ([, sample = '', analyte = '', kind = '', value = '', valueUnit = '', rule = '']) => ({
      short: 'A detection limit is too high to confirm a result',
      line: `The test for ${analyte} at ${sample} could not measure low enough to confirm it is below the ${kind} of ${value} ${unit(valueUnit)} in ${rule}.`,
    }),
    fallback: {
      short: 'A detection limit is too high to confirm a result',
      line: 'One test could not measure low enough to confirm a result is below a guideline value.',
    },
  },
};

/** The plain lines for one review item. An unknown kind of check falls back to the tool's own heading. */
export function plainLine(item: ReviewItem): PlainLine {
  const rule = RULES[item.check];
  if (rule === undefined) return { short: unit(item.title), line: unit(item.title) };
  const match = rule.pattern.exec(item.title);
  return match === null ? rule.fallback : rule.make([...match], item);
}

/** Plain names for the six checks, in the order the tool runs them. */
export const CHECK_NAMES: Readonly<Record<string, string>> = {
  'sample ids': 'Sample names match across the three files',
  units: 'Every result in one unit',
  'field duplicates': 'Repeat tests of the same sample agree',
  'holding times': 'Samples tested within their time limit',
  blanks: 'Cleaning and transport check samples are clean',
  'LOR against criteria': 'Detection limits low enough to compare with guideline values',
};

export const checkName = (check: string): string => CHECK_NAMES[check] ?? check;

/** Plain names for the three kinds of file. */
export const FILE_ROLES: Readonly<Record<string, string>> = {
  'lab results': 'Lab results',
  'chain of custody': 'Sample paperwork (chain of custody)',
  'field sheet': 'Field notes (field sheet)',
};
