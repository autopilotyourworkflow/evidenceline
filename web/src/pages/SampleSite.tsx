// /sample-site: every PFOS and PFHxS result for well MB2 with its lab file and row. Each result opens that row, read
// from the lab file itself (public/data/lab/, copied from the package by scripts/build-data.mjs), and says plainly if
// the row does not hold the value shown.

import { useEffect, useId, useState } from 'react';
import { PageShell, WhenLoaded } from '../components/PageShell';
import { SAMPLE_SITE_PAGE as P } from '../content/pages';
import { isSampleSite, useJson, type LabValue, type Round, type SampleSite } from '../lib/data';
import { formatDate, prettyUnits } from '../lib/format';
import { Link } from '../components/Link';

const HEADLINE = ['PFOS', 'PFHxS'] as const;

const labFileUrl = (file: string): string => `/data/lab/${encodeURIComponent(file)}`;

/** Each lab file is fetched once and shared by every result that opens a row from it. */
const labFiles = new Map<string, Promise<readonly string[]>>();

function labLines(file: string): Promise<readonly string[]> {
  const known = labFiles.get(file);
  if (known !== undefined) return known;
  const loading = fetch(labFileUrl(file)).then(async (response) => {
    if (!response.ok) throw new Error(`${file} could not be loaded (HTTP ${response.status}).`);
    return (await response.text()).replace(/\r\n/g, '\n').split('\n');
  });
  // A failed load is not cached, so opening the row again tries again.
  void loading.catch(() => labFiles.delete(file));
  labFiles.set(file, loading);
  return loading;
}

type RowCell = { readonly column: string; readonly value: string };
type RowState =
  | { readonly state: 'loading' }
  | { readonly state: 'ready'; readonly cells: readonly RowCell[] }
  | { readonly state: 'error'; readonly message: string };

/**
 * Row `row` of the file (the header is row 1, as in a spreadsheet) as column and value pairs, or an error when the row
 * is missing or does not hold the result shown. The lab files have no quoted fields (build-data.mjs refuses them).
 */
function readRow(lines: readonly string[], row: number, expected: LabValue, file: string): RowState {
  const header = (lines[0] ?? '').split(',');
  const line = lines[row - 1];
  if (row < 2 || line === undefined || line.trim() === '') return { state: 'error', message: `${file} has no row ${row}.` };
  const values = line.split(',');
  const cells = header.map((column, k) => ({ column: column.trim(), value: (values[k] ?? '').trim() }));
  const cell = (name: string) => cells.find((c) => c.column === name)?.value;
  if (cell('analyte') !== expected.analyte || cell('result') !== expected.reported) {
    return { state: 'error', message: `Row ${row} of ${file} does not hold ${expected.analyte} ${expected.reported}, so it is not shown.` };
  }
  return { state: 'ready', cells };
}

function LabRow({ id, file, value }: { readonly id: string; readonly file: string; readonly value: LabValue }) {
  const [row, setRow] = useState<RowState>({ state: 'loading' });
  useEffect(() => {
    let current = true;
    void labLines(file).then(
      (lines) => current && setRow(readRow(lines, value.row, value, file)),
      (error: unknown) => current && setRow({ state: 'error', message: error instanceof Error ? error.message : `${file} could not be loaded.` }),
    );
    return () => {
      current = false;
    };
  }, [file, value]);
  return (
    <div className="labrow" id={id} role="region" aria-label={`${file}, row ${value.row}`}>
      {row.state === 'loading' && <p className="muted">{P.rowLoading}</p>}
      {row.state === 'error' && <p className="status-line error">{row.message}</p>}
      {row.state === 'ready' && (
        <>
          <p className="k">{P.rowTitle(file, value.row)}</p>
          <dl>
            {row.cells.map((c) => (
              <div key={c.column}>
                <dt>{c.column}</dt>
                <dd>{c.value}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
      <a href={labFileUrl(file)}>{P.rowFileLink}</a>
    </div>
  );
}

/** A result with its file and row; pressing it opens that row of the lab file underneath. */
function Value({ value, file }: { readonly value: LabValue | undefined; readonly file: string }) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  if (value === undefined) return <span className="muted">not reported</span>;
  return (
    <div>
      <button className="rowbtn" type="button" aria-expanded={open} aria-controls={panel} onClick={() => setOpen((o) => !o)}>
        <span className="v">{value.reported}</span>
        <small className="prov">
          {file}, row {value.row}
        </small>
      </button>
      {open && <LabRow id={panel} file={file} value={value} />}
    </div>
  );
}

function ResultsTable({ site }: { readonly site: SampleSite }) {
  const unit = prettyUnits(site.unit);
  return (
    <div className="tablewrap">
      <table className="dtable stack">
        <caption className="skip">
          {P.resultsTitle}, in {unit}
        </caption>
        <thead>
          <tr>
            <th scope="col">Round</th>
            <th scope="col">Sampled</th>
            <th scope="col">Lab report</th>
            {HEADLINE.map((analyte) => (
              <th scope="col" key={analyte}>
                {analyte} ({unit})
              </th>
            ))}
            <th scope="col">Detection limit</th>
          </tr>
        </thead>
        <tbody>
          {site.rounds.map((round) => (
            <tr key={round.round}>
              <th scope="row" data-label="Round">
                {round.round}
              </th>
              <td data-label="Sampled">
                <div>
                  {formatDate(round.date)}
                  <small className="prov">{round.sample_id}</small>
                </div>
              </td>
              <td data-label="Lab report">{round.lab_report_id}</td>
              {HEADLINE.map((analyte) => (
                <td data-label={`${analyte} (${unit})`} key={analyte} className="num">
                  <Value value={round.results.find((r) => r.analyte === analyte)} file={round.file} />
                </td>
              ))}
              <td data-label="Detection limit" className="num">
                {[...new Set(round.results.map((r) => r.detection_limit))].join(', ') || 'not stated'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function rowSpan(values: readonly LabValue[]): string {
  const rows = values.map((v) => v.row);
  if (rows.length === 0) return 'none';
  return `rows ${Math.min(...rows)} to ${Math.max(...rows)}`;
}

function OtherAnalytes({ rounds }: { readonly rounds: readonly Round[] }) {
  const analytes = [...new Set(rounds.flatMap((r) => r.other_analytes.map((o) => o.analyte)))];
  const allNotDetected = rounds.every((r) => r.other_analytes.every((o) => !o.detected));
  return (
    <details className="drawer">
      <summary>
        {P.othersTitle} ({analytes.length} per file)
      </summary>
      <p className="note">
        {P.othersIntro} {allNotDetected ? P.othersAllNotDetected : P.othersSomeDetected} {P.lessThan}
      </p>
      <div className="tablewrap">
        <table className="dtable compact">
          <thead>
            <tr>
              <th scope="col">Analyte</th>
              {rounds.map((r) => (
                <th scope="col" key={r.round}>
                  {formatDate(r.date)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {analytes.map((analyte) => (
              <tr key={analyte}>
                <th scope="row">{analyte}</th>
                {rounds.map((r) => {
                  const hit = r.other_analytes.find((o) => o.analyte === analyte);
                  return (
                    <td key={r.round} className="num" data-label={formatDate(r.date)}>
                      {hit === undefined ? 'not reported' : hit.reported}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">
        {rounds.map((r) => `${r.file}: ${rowSpan(r.other_analytes)}`).join('. ')}.
      </p>
    </details>
  );
}

function SampleSiteBody({ site }: { readonly site: SampleSite }) {
  return (
    <>
      <p className="synth">
        <span aria-hidden="true">&#9679;</span> {site.synthetic_notice}
      </p>
      <section className="block" aria-labelledby="facts">
        <h2 id="facts">{P.factsTitle}</h2>
        <dl className="facts">
          <dt>Site</dt>
          <dd>
            {site.site_id}, {site.site_description.charAt(0).toLowerCase() + site.site_description.slice(1)}
          </dd>
          <dt>Well</dt>
          <dd>{site.well_id}</dd>
          <dt>Sample</dt>
          <dd>{[...new Set(site.rounds.map((r) => r.matrix))].join(', ')}</dd>
          <dt>Rounds</dt>
          <dd>{site.rounds.map((r) => formatDate(r.date)).join(', ')}</dd>
          <dt>Files</dt>
          <dd>{site.rounds.map((r) => r.path).join(', ')}</dd>
          <dt>Row numbers</dt>
          <dd>{site.row_numbering}</dd>
        </dl>
      </section>
      <section className="block" aria-labelledby="results">
        <h2 id="results">{P.resultsTitle}</h2>
        <p className="note">{P.resultsNote}</p>
        <ResultsTable site={site} />
        <OtherAnalytes rounds={site.rounds} />
      </section>
      <p className="boundary">{P.boundary}</p>
      <div className="linkrow">
        <Link className="btn ghost sm" to="/sources">
          {P.sourcesLink}
        </Link>
        <Link className="btn ghost sm" to="/tidy">
          {P.tidyLink}
        </Link>
        <a className="btn ghost sm" href="/data/sample-site.json">
          {P.rawLink}
        </a>
      </div>
    </>
  );
}

export function SampleSitePage() {
  const loaded = useJson('sample-site.json', isSampleSite);
  return (
    <PageShell title={P.title} lead={<p>{P.lead}</p>}>
      <WhenLoaded loaded={loaded}>{(site) => <SampleSiteBody site={site} />}</WhenLoaded>
    </PageShell>
  );
}
