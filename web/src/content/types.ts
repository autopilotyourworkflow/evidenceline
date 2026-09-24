// Types for the page copy. Copy lives in typed data files; components only decide how it looks.

export type GlossaryKey = 'ugPerL' | 'pfos' | 'guidelineLimit';

/** One piece of inline text: plain, bold, emphasised, a glossary term, a code-filled number, or a link. */
export type Inline =
  | string
  | { readonly b: string }
  | { readonly em: string }
  | { readonly term: GlossaryKey }
  | { readonly fill: string }
  | { readonly to: string; readonly text: string }
  | { readonly href: string; readonly text: string };

/** A run of inline text, such as one paragraph. */
export type Rich = readonly Inline[];

export type GlossaryEntry = { readonly label: string; readonly explanation: string };

export type IconName =
  | 'flask'
  | 'draft'
  | 'check'
  | 'person'
  | 'numbers'
  | 'scientist'
  | 'lock'
  | 'chart'
  | 'file'
  | 'search'
  | 'arrowDown'
  | 'chevron'
  | 'cross'
  | 'tick'
  | 'pencil';

export type NavItem = { readonly href: string; readonly label: string; readonly cta?: boolean };

export type FlowStep = { readonly icon: IconName; readonly title: string; readonly text: string };

export type Photo = { readonly src: string; readonly alt: string; readonly caption: string };

export type PreviewId = 'tidy' | 'ask' | 'check';

export type Job = {
  readonly photo: Photo;
  readonly title: string;
  readonly today: string;
  readonly withIt: string;
  readonly preview: PreviewId;
};

export type TechBlock =
  | { readonly kind: 'p'; readonly text: Rich }
  | { readonly kind: 'dl'; readonly rows: readonly (readonly [string, string])[] };

export type StepCaption = {
  readonly tab: string;
  readonly title: string;
  readonly paragraphs: readonly Rich[];
  readonly tech: readonly TechBlock[];
};

export type Pillar = { readonly icon: IconName; readonly title: string; readonly text: string };

export type CannedAnswer = {
  readonly question: string;
  readonly paragraphs: readonly Rich[];
  readonly source: string;
};

export type SiteLink = { readonly to: string; readonly label: string };
