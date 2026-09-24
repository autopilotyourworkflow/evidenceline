// Copy for the landing page, word for word from the v3 design draft (kept locally, not in the repository).

import type { AnswerKind } from '../lib/answers';
import type { CannedAnswer, FlowStep, GlossaryEntry, GlossaryKey, Job, NavItem, Pillar, Rich, SiteLink, StepCaption } from './types';

/** A no-break space, so a number and its unit stay on one line. */
const NBSP = String.fromCharCode(0xa0);

export const NAV: readonly NavItem[] = [
  { href: '#jobs', label: 'What it does' },
  { href: '#how', label: 'See an example' },
  { href: '#trust', label: 'Why trust it' },
  { href: '#about', label: 'About' },
  { href: '#try', label: 'Try it', cta: true },
];

export const HERO = {
  title: 'Every line of the report, backed by the data.',
  sub: 'Evidenceline helps environmental scientists with the slow parts of the job: tidying lab results, finding answers in the guidelines, and preparing reports. Every number comes straight from the data, every sentence is checked, and the scientist makes every call.',
  primary: 'See an example',
  secondary: 'Try it yourself',
  credit:
    'A working concept by Chanon (Beam) Poovaviranon, prepared for Western Environmental. Not affiliated with them. Made-up site and lab results; real, public guidelines.',
} as const;

export const FLOW_TITLE = 'How it works, in four steps';

export const FLOW: readonly FlowStep[] = [
  { icon: 'flask', title: 'Lab results come in', text: 'The lab tests soil or water samples and sends a results file.' },
  {
    icon: 'draft',
    title: 'Claude suggests a first draft',
    text: 'Claude suggests the wording. Code copies in every number, so none are mistyped.',
  },
  { icon: 'check', title: 'A checker tests every sentence', text: "Anything the lab results don't support is flagged, with the reason." },
  { icon: 'person', title: 'A scientist decides and signs', text: 'Every decision is recorded, so anyone can see who decided what.' },
];

export const JOBS_INTRO = {
  title: 'Three everyday jobs it takes on',
  lead: 'A contaminated-land consultancy spends a lot of skilled time on work that is slow, repetitive and easy to get wrong. These are the three Evidenceline is built for, each with a preview of its screen.',
} as const;

export const JOBS: readonly Job[] = [
  {
    photo: {
      src: '/img/lab.jpg',
      alt: 'Water samples being prepared for testing in a laboratory',
      caption: 'Water samples being prepared for testing',
    },
    title: 'Tidying up lab results',
    today:
      "the lab results, the sample paperwork and the field notes arrive separately and don't always agree. Someone lines them up by hand before anyone can use them.",
    withIt: 'it reads all three, builds one clean table, and lists every mismatch for a person to check.',
    preview: 'tidy',
  },
  {
    photo: {
      src: '/img/documents.jpg',
      alt: 'Stacks of paper files and past reports',
      caption: 'Guidelines and past reports: thousands of pages',
    },
    title: 'Finding answers in thousands of pages',
    today:
      'the guidelines and past reports run to thousands of pages, and they keep being updated. The same question gets researched again and again.',
    withIt: "ask in plain English. The answer shows the exact page it came from, or says clearly that the guidelines don't cover it.",
    preview: 'ask',
  },
  {
    photo: { src: '/img/field.jpg', alt: 'Field staff taking water readings in a wetland', caption: 'Field staff taking water readings' },
    title: 'Preparing the report',
    today:
      "reports follow a strict format and must be exactly right, because they're signed and sent to the regulator. Writing them takes days.",
    withIt: 'Claude suggests a first draft, code fills in every number, and a checker tests every sentence. The scientist edits, decides and signs.',
    preview: 'check',
  },
];

export const TIDY_PREVIEW = {
  /** The files, the item count and the lines come from tidy.json (the real tool's output), not from this file. */
  label: (files: string, count: number) =>
    `Preview of the Tidy lab results screen. ${files} been read and combined into one table, and ${count} ${count === 1 ? 'item is' : 'items are'} listed for a person to check.`,
  loadingLabel: 'Preview of the Tidy lab results screen.',
  screen: 'Tidy lab results',
  /** Short names for the kinds of file tidy.json lists; an unknown kind is shown as the tool names it. */
  fileNames: { 'lab results': 'Lab results', 'chain of custody': 'Sample paperwork', 'field sheet': 'Field notes' } as Readonly<Record<string, string>>,
  result: 'Combined into one table.',
  resultTail: (count: number) => `${count} ${count === 1 ? 'thing' : 'things'} for you to check:`,
  unavailable: 'The example result could not be loaded.',
  note: 'Preview of the screen, using the made-up sample site.',
  fullLink: 'See the full result',
} as const;

export const ASK_PREVIEW = {
  label: 'Preview of the Ask the guidelines screen. A question is typed in a search bar, and below it are the answer and its two sources with page numbers.',
  screen: 'Ask the guidelines',
  question: 'What is the drinking-water limit for PFOS?',
  answer: ['Two values, one under each rule. Current national drinking-water values (updated 2025): ', { b: `0.008${NBSP}µg/L` }, ' for PFOS on its own.'] as Rich,
  answerNote: `PFAS NEMP 3.0, named as adopted in WA: 0.07${NBSP}µg/L for PFOS and PFHxS added together, which also applies to PFOS on its own. Evidenceline shows both and picks neither.`,
  sources: [
    { name: 'PFAS NEMP 3.1', rest: ' (2026), Table 4, page 49' },
    { name: 'PFAS NEMP 3.0', rest: ' (2025), Table 4, PDF page 57' },
  ],
} as const;

export const CHECK_PREVIEW = {
  label: 'Preview of the Check a report screen. A sentence in the draft is highlighted, and a comment beside it says the claim is not supported by the lab results.',
  screen: 'Check a report',
  claim: 'PFOS levels in well MB2 increased',
  claimRest: ' between November 2024 and September 2025.',
  numbers: [
    'In September 2025, PFOS was ',
    { fill: `0.038${NBSP}µg/L` },
    ', above the June 2025 guideline value of ',
    { fill: `0.008${NBSP}µg/L` },
    '.',
  ] as Rich,
  bubbleTitle: 'Not supported',
  bubbleText: 'The lab results show PFOS went slightly down, from 0.041 to 0.038.',
  acceptLabel: 'Use suggestion',
  keepMine: 'Keep mine',
  moreLink: 'Walk through this example, step by step',
} as const;

export const GLOSSARY: Readonly<Record<GlossaryKey, GlossaryEntry>> = {
  ugPerL: {
    label: 'µg/L',
    explanation:
      'Micrograms per litre: millionths of a gram in one litre of water. PFAS limits are this small because the chemicals build up in the body over time.',
  },
  pfos: { label: 'PFOS', explanation: 'One of the most common PFAS "forever chemicals". It was widely used in firefighting foam.' },
  guidelineLimit: {
    label: 'guideline limit',
    explanation:
      "A level set in national guidelines. Going above it means a scientist looks more closely. It doesn't automatically mean the water is unsafe.",
  },
};

export const TOUR_INTRO = {
  title: 'See it work: one well, five steps',
  lead: 'A made-up example based on real guidelines. Use the steps or the Next button. Underlined words explain themselves when you tap them.',
  summaryLead: 'The whole idea in one line:',
  summary: " AI suggests the words, code supplies the numbers, a checker catches what doesn't hold up, and the scientist decides.",
} as const;

/** PFOS at well MB2 for the chart in step 1. Heights are bar heights in percent, as drawn in the design. */
export const MB2_PFOS_CHART = [
  { label: 'Mar 2024', value: '0.062', height: 86 },
  { label: 'Nov 2024', value: '0.041', height: 57 },
  { label: 'Sep 2025', value: '0.038', height: 53 },
  { label: 'May 2026', value: '0.006', height: 8 },
] as const;

/** The part of the drafted first sentence that the checker flags. */
export const FLAGGED_CLAIM = 'PFOS levels in well MB2 increased between November 2024 and September 2025';
export const ORIGINAL_SENTENCE = `${FLAGGED_CLAIM}.`;

/** The rest of the drafted paragraph: every number is copied in by code (shown highlighted). */
export const DRAFT_TAIL: Rich = [
  ' In September 2025, PFOS was ',
  { fill: `0.038${NBSP}µg/L` },
  ', above the June 2025 guideline value of ',
  { fill: `0.008${NBSP}µg/L` },
  '. In November 2024, PFOS was ',
  { fill: `0.041${NBSP}µg/L` },
  '.',
];

export const STEP_VISUALS = {
  chartTitle: [{ b: 'Well MB2' }, ', PFOS in the water at each test, in ', { term: 'ugPerL' }] as Rich,
  chartCaption: 'PFOS in well MB2',
  docTitle: 'Draft report, section 6.2: Groundwater',
  draftState: 'First draft, suggested by Claude',
  checkedState: 'Checked',
  waitingState: 'Waiting for a decision',
  recordedState: 'Decision recorded',
  srcNote: 'Copied by code from the lab file or the guideline, not typed by the AI',
  flagTitle: 'Not supported by the lab results',
  flagText: 'The sentence says PFOS went up. The lab results say it went slightly down, from 0.041 to 0.038.',
  okTitle: '3 numbers checked',
  okText: 'Each one matches its lab file row or guideline page.',
  rulerTitle: ['The ', { b: 'same water sample' }, ' (September 2025), measured against the rule before and after June 2025'] as Rich,
  rulers: [
    {
      name: 'Rule before June 2025',
      basis: 'PFOS and a similar chemical added together',
      fill: '71.25%',
      limitAt: '87.5%',
      limitLabel: 'limit 0.07',
      over: null,
      verdict: '0.057 in the water: under the limit',
    },
    {
      name: 'Rule from June 2025',
      basis: 'PFOS on its own',
      fill: '47.5%',
      limitAt: '10%',
      limitLabel: 'limit 0.008',
      over: '21.05%',
      verdict: '0.038 in the water: over the limit',
    },
  ],
  scale: ['0', '0.02', '0.04', '0.06', '0.08 µg/L'],
  suggestTitle: 'Suggested sentence',
  accept: 'Use the suggested sentence',
  keep: 'Keep mine, with a reason',
  reasonLabel: 'Why keep the original? This is recorded with your name.',
  save: 'Save the decision',
} as const;
export const SUGGESTED_SENTENCE =
  'PFOS in well MB2 was slightly lower in September 2025 than in November 2024. The September 2025 result is above the drinking-water value for PFOS set in June 2025, and below the PFAS NEMP 3.0 value, which the WA government page names as adopted.';

export const STEPS: readonly StepCaption[] = [
  {
    tab: 'The lab results',
    title: 'A lab tests water from one well, four times.',
    paragraphs: [
      ['The chart shows ', { term: 'pfos' }, ' at each test. Each result was lower than the one before.'],
      ['Next, Claude will write the paragraph about these results that goes in the report.'],
    ],
    tech: [
      {
        kind: 'dl',
        rows: [
          ['Site', 'FDS-01, a fictional former depot, Perth'],
          ['Sample', 'Groundwater, bore MB2'],
          ['Tests', '12 Mar 2024, 19 Nov 2024, 16 Sep 2025, 5 May 2026'],
          ['PFHxS', '0.021, 0.018, 0.019, 0.009 µg/L'],
          ['Detection limit', '0.001 µg/L'],
        ],
      },
      { kind: 'p', text: ['Every result opens its lab file row on the ', { to: '/sample-site', text: 'full sample site' }, '.'] },
    ],
  },
  {
    tab: 'Claude suggests',
    title: 'Claude suggests the wording. Code fills in the numbers.',
    paragraphs: [
      [
        "Claude never types a number. It leaves a placeholder, and code copies the exact value from the lab file or the guideline. A mistyped number can't get into the report.",
      ],
      ["It reads well. But is every sentence true? That's the next step."],
    ],
    tech: [
      {
        kind: 'p',
        text: [
          'Claude writes placeholders such as ',
          { em: 'PFOS, MB2, Sep 2025' },
          '. Python resolves each one against the lab file and the guideline table, and records the source row or page next to the number.',
        ],
      },
    ],
  },
  {
    tab: 'The check',
    title: 'The checker compares every sentence with the data.',
    paragraphs: [
      [
        'The numbers are right. But the first sentence claims PFOS went up, and the lab results show it went slightly down. The checker stops that sentence before anyone signs it.',
      ],
      ['So why did the draft say it got worse? Because the result went from under the limit to over it. Step 4 shows why that happened.'],
    ],
    tech: [
      {
        kind: 'p',
        text: [
          { b: 'What the checker tests:' },
          ' every number traced to a source; well names and test dates; words about direction and comparison (increased, decreased, above, below, exceeds).',
        ],
      },
      {
        kind: 'p',
        text: [
          { b: 'What it does not test:' },
          ' whether the chosen guideline is the right one for this site. That stays with the scientist. The checker never reports "no issues found"; it lists what it checked.',
        ],
      },
    ],
  },
  {
    tab: 'Why it looked worse',
    title: "The water didn't get worse. The limit changed.",
    paragraphs: [
      [
        "In June 2025, Australia's drinking-water guidelines set a much stricter ",
        { term: 'guidelineLimit' },
        ' for PFOS. Same water, different ruler.',
      ],
      [
        'Evidenceline shows the result under both rules and leaves the choice to the scientist, because which rule applies to a report is their professional call.',
      ],
    ],
    tech: [
      {
        kind: 'p',
        text: [{ b: 'Before:' }, ' PFAS NEMP 3.0 (March 2025), Table 4: drinking water 0.07 µg/L for the sum of PFOS and PFHxS, which footnote a applies to PFOS only, PFHxS only and the sum. This round: 0.038 + 0.019 = 0.057, the largest of the three.'],
      },
      {
        kind: 'p',
        text: [
          { b: 'From June 2025:' },
          ' ADWG as updated in 2025, which PFAS NEMP 3.1 (2026) uses, Table 4, printed page 49: PFOS 0.008 µg/L and PFHxS 0.03 µg/L, screened separately.',
        ],
      },
      { kind: 'p', text: ['The WA government PFAS page names NEMP 3.0 as adopted for implementation in WA. Evidenceline shows both and does not choose.'] },
      { kind: 'p', text: [{ href: 'https://www.dcceew.gov.au/sites/default/files/documents/pfas-nemp-3-1.pdf', text: 'PFAS NEMP 3.1 (PDF)' }] },
    ],
  },
  {
    tab: 'A person decides',
    title: "A person makes the call, and it's recorded.",
    paragraphs: [
      [
        'Evidenceline suggests a sentence that matches the data. The scientist can use it or keep their own with a reason. Either way the decision is saved with their name, so a reviewer or auditor can see who decided what.',
      ],
      ['Try it: choose one of the two buttons.'],
    ],
    tech: [
      {
        kind: 'p',
        text: [
          'Decisions go to a review log that exports with the report as a QA appendix: the finding, the decision (accept or override), the reason, initials and time.',
        ],
      },
    ],
  },
];

export const TRUST = {
  title: 'Built to be trusted by the people who sign',
  lead: 'Scientists are right to be careful with AI. Evidenceline is designed around that.',
} as const;

export const PILLARS: readonly Pillar[] = [
  {
    icon: 'numbers',
    title: "Numbers can't be mistyped",
    text: 'Every number is copied from the data by code, with the lab file row or guideline page it came from.',
  },
  {
    icon: 'scientist',
    title: "It doesn't make the scientist's calls",
    text: 'Which guideline applies, and what a result means for a site, stay with the person who signs.',
  },
  {
    icon: 'lock',
    title: 'Client details stay private',
    text: 'Client and site names are hidden before anything reaches the AI, and restored only on your own computer.',
  },
  { icon: 'chart', title: 'It shows its own mistakes', text: 'The results of its main automated tests are published, including the ones that fail.' },
];

export const TRY = {
  title: 'Try it yourself',
  /**
   * The line under the heading. It counts only the ways that work in this build, so the launch build (question box
   * and connector not switched on yet) promises nothing it then takes back.
   */
  lead: (askOn: boolean, connectOn: boolean): string =>
    askOn && connectOn
      ? 'Two ways, no account needed.'
      : askOn
        ? 'No account needed: ask the guidelines below. Using it inside Claude is switching on soon.'
        : connectOn
          ? 'No account needed: add it to your own Claude, or pick a prepared question below. Typed questions are switching on soon.'
          : 'No account needed: pick a prepared question below. Typed questions and using it inside Claude are switching on soon.',
  askTitle: 'Ask the guidelines a question',
  askText: 'Answers come only from the public guidelines, with the page they rely on.',
  inputLabel: 'Your question',
  placeholder: 'Type a question in plain English',
  /** Shown in the box, before anything is typed, while live answers are not switched on (VITE_API_BASE is empty). */
  comingSoon: 'Live answers to typed questions are switching on soon. Until then, pick one of the prepared questions below.',
  /** Shown for a typed question while live answers are not switched on. */
  comingSoonMessage: 'Live answers are switching on soon. Until then, try one of the prepared questions above.',
  /** A live question found no token from the "not a robot" check (Turnstile) in time, so it was not sent. */
  robotWaiting: 'The check that you are not a robot has not finished yet. If the check above asks you to tick a box, tick it, then ask again.',
  robotUnavailable:
    'The check that you are not a robot could not load in this browser, so the question was not sent. The prepared questions above still work.',
  robotLabel: 'Check that you are not a robot',
  loading: 'Looking through the guidelines. This can take up to a minute.',
  rateLimited: (wait: string) =>
    `Too many questions in a short time. Please try again${wait}, or pick a prepared example above.`,
  paused: 'Live answers are paused for now. The prepared examples above still work.',
  errorTail: 'Please try again later, or pick a prepared example above.',
  /** The small print under a live result; only a checked answer is called "answered". */
  liveNote: (kind: AnswerKind, hasSources: boolean): string =>
    kind === 'answered'
      ? 'Answered live from the public guidance. Open a source to check the page it relied on.'
      : kind === 'passages-only' || kind === 'paused' || kind === 'error'
        ? `Live result: no written answer was given.${hasSources ? ' Open a passage to check its page.' : ''}`
        : 'Live result from the public guidance search.',
  preparedNote: (when: string) => `Prepared in advance by the same pipeline${when}.`,
  preparedIntro: 'Suggested questions were answered in advance by the same pipeline as the question box.',
  fallbackIntro: 'These two examples are written by hand from the guideline values table.',
  titles: {
    'passages-only': 'No written answer this time: here are the passages it found',
    'not-covered': 'Not covered by the guidance it can read',
    'guard-rail': "Evidenceline won't answer that",
    paused: 'Live answers are paused',
    error: 'The question could not be answered',
  },
  notCoveredDefault: 'The indexed guidance does not cover this question, so Evidenceline does not guess.',
  guardRailDefault:
    'Whether a site is contaminated or water is unsafe is a professional judgement. Evidenceline can show which results are above or below each guideline and where each value comes from.',
  valuesTitle: 'Guideline values',
  sourcesTitle: 'Sources',
  passagesTitle: 'Passages from the guidance',
  otherPassages: (n: number) => `${n} more ${n === 1 ? 'passage' : 'passages'} it was given`,
  checksTitle: 'How this answer was made and checked',
  redacted: (n: number) =>
    `${n} ${n === 1 ? 'detail' : 'details'} in the question (such as a company or site name, address, lot number, email or phone number) ${n === 1 ? 'was' : 'were'} replaced with a placeholder before searching.`,
  connectTitle: 'Use it inside your own Claude',
  connectText: 'If you use Claude, you can add Evidenceline to it and ask in your own words.',
  /** The card's text while the connector link is not switched on (VITE_MCP_URL is empty). */
  connectTextLater: 'Once the connector is switched on, anyone who uses Claude will be able to add Evidenceline to it and ask in their own words.',
  copyDone: 'Copied',
  copyIdle: 'Copy',
  copyFailed: 'Not copied',
  copyFailedNote: 'Your browser did not allow copying, so the link above is selected. Copy it from there.',
  connectLater: 'The connector link will appear here soon. It will be a read-only link you paste into Claude.',
  connectSteps: [
    'Copy this link.',
    'In Claude, open Settings, then Connectors, then "Add custom connector", and paste it.',
    'Ask something like: "Check this paragraph against well MB2\'s results."',
  ],
} as const;

/** Hand-written answers for two example questions, shown only when answers.json is missing or unreadable. */
export const EXAMPLE_ANSWERS: readonly CannedAnswer[] = [
  {
    question: 'What is the drinking-water limit for PFOS?',
    paragraphs: [
      [
        'There is one value under each rule. Under the current drinking-water values (Australian Drinking Water Guidelines as updated in 2025, listed in PFAS NEMP 3.1): ',
        { b: '0.008 µg/L' },
        ' (8 nanograms per litre), compared with PFOS on its own.',
      ],
      [
        'Under PFAS NEMP 3.0 (March 2025), which the Western Australian government PFAS page names as adopted: ',
        { b: '0.07 µg/L' },
        ', compared with PFOS on its own, PFHxS on its own and the sum of the two.',
      ],
      ["Evidenceline picks neither. Which one a report uses is the scientist's call."],
    ],
    source: 'Sources: PFAS NEMP 3.1, Table 4, printed page 49. PFAS NEMP 3.0, Table 4 and its footnote a, PDF page 57.',
  },
  {
    question: 'Is this site contaminated?',
    paragraphs: [
      [
        { b: "Evidenceline won't answer that." },
        ' Whether a site is contaminated is a professional judgement, and in WA the classification is made by the regulator. Evidenceline can show which results are above or below each guideline and where each value comes from.',
      ],
    ],
    source: 'Why: the national guidelines say investigation levels are not clean-up levels (ASC NEPM, Schedule B1).',
  },
];

export const ABOUT = {
  title: 'Why I built this',
  paragraphs: [
    [
      "I'm ",
      { b: 'Chanon (Beam) Poovaviranon' },
      ", an AI engineer based in Bangkok. I built Evidenceline to show how I'd approach AI at an environmental consultancy: start with the slow, error-prone jobs, build tools scientists can check for themselves, test them in public, and explain them in plain English.",
    ],
    [
      "It's a concept on a made-up site. With a firm's own lab files, reports and templates, the same approach would run on their real work, inside the Claude they already use.",
    ],
  ] as readonly Rich[],
  underTitle: 'Under the hood',
  under: [
    'Python and the Model Context Protocol, so it runs inside Claude',
    'Checks written as code, not AI guesses',
    'Automated tests, with the results published',
    'Open source (Apache-2.0)',
  ],
  scientistsTitle: 'For scientists',
  forScientists: [
    { to: '/sample-site', label: 'Full sample site, with the source of every number' },
    { to: '/tidy', label: 'Tidy lab results: the full example' },
    { to: '/sources', label: 'Guideline sources, editions and pages' },
    { to: '/accuracy', label: 'Accuracy results' },
  ] as readonly SiteLink[],
  /** Shown only when VITE_GITHUB_URL is set (see lib/config.ts). */
  codeLabel: 'Code on GitHub',
  portfolioLabel: 'My portfolio',
  portfolioHref: 'https://autopilotyourworkflow.com/work',
} as const;
