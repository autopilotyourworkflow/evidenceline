// Fixed copy for the detail pages. Data values come from public/data, never from here.

export const SAMPLE_SITE_PAGE = {
  title: 'Full sample site, with the source of every number',
  lead: 'Every PFOS and PFHxS result for well MB2, with the lab report, file and row it came from.',
  factsTitle: 'The site',
  resultsTitle: 'PFOS and PFHxS at well MB2',
  resultsNote: 'Values as reported by the lab. Each one shows the file and row it was read from; press a value to open that row.',
  rowLoading: 'Opening the row in the lab file.',
  rowTitle: (file: string, row: number) => `${file}, row ${row}, as the lab file has it`,
  rowFileLink: 'Open the whole lab file (CSV)',
  othersTitle: 'Other analytes in each file',
  othersIntro: 'Each lab file also reports other PFAS.',
  othersAllNotDetected: 'Every one of them is below its detection limit in every round.',
  othersSomeDetected: 'Some of them were detected; the values are shown as reported.',
  lessThan: 'A "<" value means not detected at that detection limit.',
  boundary:
    'These are lab results only. A guideline value is an investigation level, so a result above one means a scientist looks more closely. Evidenceline does not say whether the water is unsafe or the site is contaminated.',
  sourcesLink: 'Guideline values and where they come from',
  tidyLink: 'Tidy lab results: the full example',
  rawLink: 'Download this data (JSON)',
} as const;

export const TIDY_PAGE = {
  title: 'Tidy lab results: the full example',
  lead: 'What the Tidy lab results tool returns for the made-up sample site, in full. Each item starts with one plain sentence; open "Technical detail" for the evidence, the rule and its source.',
  filesTitle: (count: string, n: number) => (n === 1 ? 'The file it read' : `The ${count} files it read`),
  filesNote: 'Each file writes dates its own way. Evidenceline reads them all day first and keeps the row number of every value.',
  itemsTitle: 'things for a person to check',
  itemsNote:
    'Each item is something the files disagree on or a quality check that did not pass. Evidenceline explains it and stops there: the scientist decides what to do.',
  toolHeading: "The tool's own heading",
  found: 'What was found',
  evidence: 'Evidence (file and row)',
  rule: 'The rule',
  source: 'Source',
  decides: 'What the scientist decides',
  checkedTitle: 'Checked and not flagged',
  checkedNote: 'Every check lists what it looked at, so nothing passes silently.',
  /** Shown when tidy.json carries only the number of things checked and not flagged, not the list itself. */
  checkedCountOnly: "This page's copy of the result keeps only that count; the tool's full result lists each one.",
  notCheckedTitle: 'Not checked',
  notCheckedNote: 'What the tidy step did not look at, and why. It says so rather than guessing.',
  notesTitle: 'Ground rules the tool states',
  summaryTitle: "The tool's summary, word for word",
  boundary:
    "A guideline value is an investigation level, not a finding that water is unsafe or a site is contaminated. Two drinking-water rules are shown side by side; which one applies is the scientist's call.",
  sourcesLink: 'Guideline values and where they come from',
  rawLink: 'Download this result (JSON)',
} as const;

export const SOURCES_PAGE = {
  title: 'Guideline sources, editions and pages',
  lead: 'The drinking-water values for PFAS in groundwater that Evidenceline screens against, and the soil values the tidy step uses, mainly to check detection limits. Each value carries its document, table and page.',
  choiceTitle: 'Two rules, side by side',
  investigationNote:
    'A guideline value is an investigation level, not a clean-up level. A result above it means a scientist looks more closely. It is not a finding that the water is unsafe or that the site is contaminated.',
  waterTitle: 'Drinking-water values for groundwater',
  valuesTitle: 'Values',
  linksTitle: 'Source documents',
  soilTitle: 'Soil values, used only inside the tidy step',
  soilLandUse:
    "HIL A is the health investigation level for residential land with a garden. The made-up site's scenario names that land use; which land use applies to a real site is the scientist's call.",
  notLoaded:
    'Groundwater is screened against drinking-water values only. The soil values above are used only inside the tidy step; no tool screens detected soil results against them. Recreational water and ecological criteria are not loaded, so nothing is checked against them.',
  rawLink: 'Download the water values (JSON)',
  soilRawLink: 'Download the soil values (JSON)',
} as const;

export const ACCURACY_PAGE = {
  title: 'Accuracy results',
  lead: 'What the automated tests check, and what they found. Failures are shown as well as passes.',
  resultsTitle: 'Results',
  inShortTitle: 'In short',
  testsTitle: 'Automated tests',
  testsNote: 'Counts by area of the code. A failed test is listed here as a failure, not hidden.',
  searchTitle: 'Guidance search',
  searchNote:
    'How often the right page comes back for a set of test questions. The held-out set was never used to adjust the search, so it is the fair score. The two tuning sets were used to adjust it, so they flatter it.',
  verifyTitle: 'Guideline values, checked twice',
  verifyNote: 'Each value was compared with its source page by two separate automated passes. Any disagreement is shown.',
  notesTitle: 'Notes next to the values',
  notesNote:
    'Each value comes with a short explanatory note. The first two passes did not confirm these notes, so they were reworded to follow the source. Two later automated re-checks then read the source again and judged the new wording. The values themselves are listed above.',
  findingsTitle: 'Other findings from the re-checks',
  findingsNote:
    'What the re-checks judged besides those notes. A finding fixed afterwards says how; its verdict stays as the re-check recorded it.',
  missesLabel: 'Questions it missed',
  methodTitle: 'How guideline values are checked',
  /** PRODUCT.md: the Accuracy page must say exactly this. */
  method: 'Guideline values are checked by two independent automated passes against the source pages, not reviewed by a practitioner.',
  notCheckedTitle: 'What Evidenceline does not check',
  notCheckedNote: 'These are out of scope. The tool says so rather than guessing.',
  notChecked: [
    'Ecological investigation levels',
    'Petroleum screening levels',
    'Recreational water criteria',
    'Detected soil results against soil criteria (soil HIL A values are loaded, but used only inside the tidy step, mainly to check detection limits)',
    'Site maps',
    'Statistics and trends',
    'Site classification',
    'Compliance verdicts',
  ],
  sourcesLink: 'See the guideline values and their pages',
} as const;

export const NOT_FOUND_PAGE = {
  title: 'Page not found',
  lead: 'There is no page at this address.',
  home: 'Go to the home page',
} as const;
