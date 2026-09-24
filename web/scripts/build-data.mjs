// Builds web/public/data/sample-site.json, sources.json and soil-criteria.json from the Python package's own data
// files, so the website shows exactly the values the MCP server uses. It also copies the well MB2 lab files, byte for
// byte apart from line endings, to web/public/data/lab/, so each result on /sample-site can open the row it came from.
// sample-site.json and sources.json are also written by scripts/export_web_data.py (the two must agree byte for byte);
// soil-criteria.json is written only here.
//
//   node scripts/build-data.mjs           write the JSON files and the lab file copies
//   node scripts/build-data.mjs --check   fail (exit 1) if any of them is out of date
//
// Values stay strings from start to finish: nothing here parses a concentration into a float.
// accuracy.json, tidy.json, search-example.json and answers.json are not generated here (see the repository's DEVNOTES).

import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repoDir = resolve(webDir, '..');
const dataDir = join(repoDir, 'src', 'evidenceline', 'data');
const outDir = join(webDir, 'public', 'data');

const REQUIRED = ['lab_report_id', 'site_id', 'well_id', 'sample_id', 'sample_date', 'matrix', 'analyte', 'result', 'unit', 'lor'];
const HEADLINE = ['PFOS', 'PFHxS'];
const NUMBER = /^<?\d+(\.\d+)?$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

// Links to the primary sources, as recorded in the verified criteria research (2026-09-23).
const SOURCE_LINKS = {
  'nemp-3.0': [
    {
      label: 'PFAS NEMP 3.0 (PDF, archived copy of the DCCEEW file)',
      url: 'https://web.archive.org/web/20250315173805/https://www.dcceew.gov.au/sites/default/files/documents/pfas-nemp-3.pdf',
    },
  ],
  current: [
    { label: 'PFAS NEMP 3.1 (PDF)', url: 'https://www.dcceew.gov.au/sites/default/files/documents/pfas-nemp-3-1.pdf' },
    {
      label: 'NHMRC drinking-water guideline fact sheet for PFAS',
      url: 'https://guidelines.nhmrc.gov.au/australian-drinking-water-guidelines/part-5/physical-chemical-characteristics/cas-numbers-1763-23-1-pfos-335-67-1-pfoa-355-46-4-pfhxs',
    },
  ],
};

function fail(message) {
  console.error(`build-data: ${message}`);
  process.exit(1);
}

function parseCsv(path) {
  const lines = readFileSync(path, 'utf8').replace(/\r\n/g, '\n').split('\n').filter((l) => l.trim() !== '');
  if (lines.some((l) => l.includes('"'))) fail(`${path}: quoted fields are not supported by this simple reader`);
  const header = lines[0].split(',');
  for (const col of REQUIRED) if (!header.includes(col)) fail(`${path}: missing column "${col}"`);
  // The header is row 1, as in a spreadsheet, so the first data line is row 2.
  return lines.slice(1).map((line, k) => {
    const cells = line.split(',');
    if (cells.length !== header.length) fail(`${path} row ${k + 2}: expected ${header.length} cells, found ${cells.length}`);
    return { row: k + 2, ...Object.fromEntries(header.map((h, j) => [h, cells[j].trim()])) };
  });
}

function one(rows, key, file) {
  const values = [...new Set(rows.map((r) => r[key]))];
  if (values.length !== 1) fail(`${file}: expected one ${key}, found ${values.join(', ')}`);
  return values[0];
}

/** The well MB2 lab files, round by round. */
function labFiles() {
  const files = readdirSync(dataDir)
    .map((name) => ({ name, m: /^mb2_round(\d+)_lab\.csv$/.exec(name) }))
    .filter((f) => f.m)
    .sort((a, b) => Number(a.m[1]) - Number(b.m[1]));
  if (files.length === 0) fail(`no mb2_round*_lab.csv files in ${dataDir}`);
  return files;
}

/** Each lab file as the site serves it (lab/<file>), with "\n" line endings so the check is stable. */
function buildLabCopies() {
  return Object.fromEntries(labFiles().map(({ name }) => [`lab/${name}`, readFileSync(join(dataDir, name), 'utf8').replace(/\r\n/g, '\n')]));
}

function buildSampleSite() {
  const files = labFiles();

  const rounds = files.map(({ name, m }) => {
    const rows = parseCsv(join(dataDir, name));
    for (const r of rows) {
      if (!NUMBER.test(r.result)) fail(`${name} row ${r.row}: result "${r.result}" is not a number or <number>`);
      if (!NUMBER.test(r.lor) || r.lor.startsWith('<')) fail(`${name} row ${r.row}: detection limit "${r.lor}" is not a number`);
      if (r.unit !== 'ug/L') fail(`${name} row ${r.row}: unit "${r.unit}" (this page expects ug/L)`);
      if (!ISO_DATE.test(r.sample_date)) fail(`${name} row ${r.row}: date "${r.sample_date}" is not YYYY-MM-DD`);
    }
    const toResult = (r) => ({
      analyte: r.analyte,
      reported: r.result,
      detected: !r.result.startsWith('<'),
      detection_limit: r.lor,
      unit: r.unit,
      row: r.row,
    });
    const headline = HEADLINE.map((analyte) => {
      const hit = rows.filter((r) => r.analyte === analyte);
      if (hit.length !== 1) fail(`${name}: expected one ${analyte} row, found ${hit.length}`);
      return toResult(hit[0]);
    });
    return {
      round: Number(m[1]),
      date: one(rows, 'sample_date', name),
      sample_id: one(rows, 'sample_id', name),
      lab_report_id: one(rows, 'lab_report_id', name),
      site_id: one(rows, 'site_id', name),
      well_id: one(rows, 'well_id', name),
      matrix: one(rows, 'matrix', name),
      file: name,
      path: relative(repoDir, join(dataDir, name)).replaceAll('\\', '/'),
      results: headline,
      other_analytes: rows.filter((r) => !HEADLINE.includes(r.analyte)).map(toResult),
    };
  });

  const sites = [...new Set(rounds.map((r) => r.site_id))];
  const wells = [...new Set(rounds.map((r) => r.well_id))];
  if (sites.length !== 1 || wells.length !== 1) fail('expected one site and one well across all rounds');

  return {
    synthetic: true,
    synthetic_notice:
      'Synthetic data. FDS-01 is a made-up site and these are made-up lab results, created to show how Evidenceline works.',
    site_id: sites[0],
    site_description: 'A fictional former depot, Perth',
    well_id: wells[0],
    unit: 'ug/L',
    row_numbering: 'The header is row 1, as in a spreadsheet.',
    rounds,
  };
}

function buildSources() {
  const raw = JSON.parse(readFileSync(join(dataDir, 'guidelines.json'), 'utf8'));
  const verified = /Verified (\d{4}-\d{2}-\d{2})/.exec(raw._about ?? '');
  if (!verified) fail('guidelines.json: _about does not state a verification date');
  return {
    verified_on: verified[1],
    about: raw._about,
    choice_note:
      'Evidenceline shows both rules side by side and does not choose between them. Which rule a report uses is the scientist\'s call.',
    rules: raw.rules.map((rule) => {
      for (const limit of rule.limits) {
        if (typeof limit.value !== 'string' || !NUMBER.test(limit.value)) fail(`${rule.id} ${limit.key}: value must be a numeric string`);
      }
      return {
        id: rule.id,
        name: rule.name,
        document: rule.document,
        table: rule.table,
        page: rule.page,
        page_basis: rule.page_basis,
        wa_status: rule.wa_status,
        links: SOURCE_LINKS[rule.id] ?? [],
        limits: rule.limits.map((l) => ({
          key: l.key,
          applies_to: l.applies_to,
          members: l.members,
          value: l.value,
          unit: l.unit,
          scenario: l.scenario,
          note: l.note,
        })),
      };
    }),
  };
}

const SOIL_UNIT = 'mg/kg';

/** Soil HIL A values from the FDS-01 site folder. The tidy step uses them for two things only (see `used_for`). */
function buildSoilCriteria() {
  const file = join(dataDir, 'fds01_site', 'soil_criteria.json');
  const raw = JSON.parse(readFileSync(file, 'utf8'));
  const verified = /compiled (\d{4}-\d{2}-\d{2})/.exec(raw._about ?? '');
  if (!verified) fail('soil_criteria.json: _about does not state when the criteria set was compiled');
  if (!Array.isArray(raw.criteria) || raw.criteria.length === 0) fail('soil_criteria.json: no criteria');
  return {
    verified_on: verified[1],
    about: raw._about,
    scenario: raw.scenario,
    scenario_short: raw.scenario_short,
    used_for:
      'Used only in the tidy step: to check that detection limits are low enough to compare with these values, and to mark a soil result linked to a blank as marginal. No tool screens detected soil results against them.',
    criteria: raw.criteria.map((c) => {
      if (typeof c.value !== 'string' || !NUMBER.test(c.value) || c.value.startsWith('<')) fail(`soil_criteria.json ${c.id}: value must be a numeric string`);
      if (c.unit !== SOIL_UNIT) fail(`soil_criteria.json ${c.id}: unit "${c.unit}" (expected ${SOIL_UNIT})`);
      for (const key of ['id', 'label', 'document', 'table', 'page']) {
        if (typeof c[key] !== 'string' || c[key] === '') fail(`soil_criteria.json ${c.id}: missing ${key}`);
      }
      return {
        id: c.id,
        label: c.label,
        members: c.members,
        each_member_too: c.each_member_too === true,
        value: c.value,
        unit: c.unit,
        document: c.document,
        table: c.table,
        page: c.page,
        note: c.note ?? '',
      };
    }),
  };
}

const asJson = (data) => `${JSON.stringify(data, null, 2)}\n`;
const outputs = {
  'sample-site.json': asJson(buildSampleSite()),
  'sources.json': asJson(buildSources()),
  'soil-criteria.json': asJson(buildSoilCriteria()),
  ...buildLabCopies(),
};

const check = process.argv.includes('--check');
let stale = 0;
for (const [name, text] of Object.entries(outputs)) {
  const path = join(outDir, name);
  if (check) {
    let current = '';
    try {
      current = readFileSync(path, 'utf8');
    } catch {
      /* missing counts as stale */
    }
    if (current.replace(/\r\n/g, '\n') !== text) {
      console.error(`build-data: ${name} is out of date; run npm run data`);
      stale += 1;
    }
  } else {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, text);
    console.log(`wrote ${relative(webDir, path)}`);
  }
}
if (stale > 0) process.exit(1);
if (check) console.log(`build-data: ${Object.keys(outputs).join(', ')} match the source files`);
