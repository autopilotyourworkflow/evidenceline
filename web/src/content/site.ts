// Copy shared by every page: brand, labelling and footer.

export const BRAND = { name: 'Evidenceline', tag: 'concept' } as const;

/** The labelling line every page carries, in the owner's wording (PRODUCT.md). */
export const CONCEPT_NOTICE =
  'Concept by Chanon (Beam) Poovaviranon, not affiliated with Western Environmental. Fictional site, public guidance only.';

/** The plain link back to the landing page on every detail page. */
export const BACK_TO_OVERVIEW = 'Back to the overview';

export const FOOTER = {
  lead: ' is a working concept by Chanon (Beam) Poovaviranon, prepared for Western Environmental. Not affiliated with Western Environmental.',
  tail: 'Made-up site and lab results. Real, public guidelines. Photos: see ',
  creditsLabel: 'credits',
  creditsHref: '/img/credits.md',
} as const;

export const PAGE_TITLES = {
  home: 'Evidenceline',
  'sample-site': 'Sample site | Evidenceline',
  tidy: 'Tidy lab results | Evidenceline',
  sources: 'Guideline sources | Evidenceline',
  accuracy: 'Accuracy | Evidenceline',
  'not-found': 'Page not found | Evidenceline',
} as const;
