// Display helpers. Values are shown exactly as stored; nothing here does arithmetic on a concentration.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] as const;

/** "2024-03-12" becomes "12 Mar 2024". Anything else is returned unchanged. */
export function formatDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return iso;
  const [, year, month, day] = match;
  const name = MONTHS[Number(month) - 1];
  return name === undefined ? iso : `${Number(day)} ${name} ${year}`;
}

/** The data files write micrograms per litre as "ug/L"; the page shows the proper symbol. */
export function prettyUnits(text: string): string {
  return text.replace(/\bug\/L\b/g, 'µg/L');
}

/** "<0.001" becomes "not detected (below 0.001)"; a detected value is shown as reported. */
export function describeReported(reported: string): string {
  return reported.startsWith('<') ? `not detected (below ${reported.slice(1)})` : reported;
}

const NUMBER_WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'] as const;

/** 3 becomes "three" (zero to ten written out, as in running text); larger counts stay as digits. */
export function numberWord(n: number): string {
  return NUMBER_WORDS[n] ?? String(n);
}

/** "three" becomes "Three". */
export const capitalise = (text: string): string => text.charAt(0).toUpperCase() + text.slice(1);
