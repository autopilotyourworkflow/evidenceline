// /tidy: the full tidy_lab_files result for the synthetic site FDS-01, read from public/data/tidy.json.
// Plain English first; each review item's evidence, rule, source and the scientist's decision are one click away.

import type { ReactNode } from 'react';
import { Icon } from '../components/Icon';
import { Link } from '../components/Link';
import { PageShell, WhenLoaded } from '../components/PageShell';
import { TIDY_PAGE as P } from '../content/pages';
import { isTidy, useJson, type ReviewItem, type Tidy, type TidyCheckedItem } from '../lib/data';
import { numberWord, prettyUnits } from '../lib/format';
import { checkName, FILE_ROLES, plainLine } from '../lib/tidyPlain';

/** "lab_results.csv row 20", "lab_results.csv row 21" become "lab_results.csv: rows 20, 21", keeping the tool's order. */
function groupEvidence(evidence: readonly string[]): string[] {
  const groups = new Map<string, string[]>();
  const loose: string[] = [];
  for (const line of evidence) {
    const match = /^(.+?) row (\d+)$/.exec(line);
    if (match === null) {
      loose.push(line);
      continue;
    }
    const [, file = '', row = ''] = match;
    const rows = groups.get(file) ?? [];
    rows.push(row);
    groups.set(file, rows);
  }
  return [...[...groups].map(([file, rows]) => `${file}: ${rows.length === 1 ? 'row' : 'rows'} ${rows.join(', ')}`), ...loose];
}

const checkedCount = (tidy: Tidy): number =>
  typeof tidy.checked_not_flagged === 'number' ? tidy.checked_not_flagged : tidy.checked_not_flagged.length;

const checkedList = (tidy: Tidy): readonly TidyCheckedItem[] =>
  typeof tidy.checked_not_flagged === 'number' ? [] : tidy.checked_not_flagged;

function Stats({ tidy }: { readonly tidy: Tidy }) {
  const stats = [
    { n: tidy.files.length, label: 'files read' },
    { n: tidy.results, label: `lab results for ${tidy.samples} samples, in one table` },
    { n: tidy.review_items.length, label: 'things for a person to check' },
    { n: checkedCount(tidy), label: 'things checked and not flagged' },
  ];
  return (
    <ul className="stats">
      {stats.map((s) => (
        <li key={s.label}>
          <b>{s.n}</b>
          <span>{s.label}</span>
        </li>
      ))}
    </ul>
  );
}

function Files({ tidy }: { readonly tidy: Tidy }) {
  return (
    <section className="block" aria-labelledby="files">
      <h2 id="files">{P.filesTitle(numberWord(tidy.files.length), tidy.files.length)}</h2>
      <p className="note">{P.filesNote}</p>
      <div className="tablewrap">
        <table className="dtable stack">
          <thead>
            <tr>
              <th scope="col">What it is</th>
              <th scope="col">File</th>
              <th scope="col">Rows read</th>
              <th scope="col">Dates written as</th>
            </tr>
          </thead>
          <tbody>
            {tidy.files.map((f) => (
              <tr key={f.file}>
                <th scope="row" data-label="What it is">
                  {FILE_ROLES[f.role] ?? f.role}
                </th>
                <td data-label="File">
                  <div>
                    {f.file}
                    <small className="prov">header on row {f.header_row}</small>
                  </div>
                </td>
                <td data-label="Rows read" className="num">
                  {f.data_rows}
                </td>
                <td data-label="Dates written as">{f.date_example}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Field({ label, children }: { readonly label: string; readonly children: ReactNode }) {
  return (
    <div className="tfield">
      <span className="k">{label}</span>
      {children}
    </div>
  );
}

function Item({ item }: { readonly item: ReviewItem }) {
  const plain = plainLine(item);
  return (
    <li className="ritem" id={`item-${item.number}`}>
      <span className="rnum" aria-hidden="true">
        {item.number}
      </span>
      <div>
        <p className="plain">
          <span className="skip">Item {item.number}: </span>
          {plain.line}
        </p>
        <details>
          <summary>
            <Icon name="chevron" />
            Technical detail
          </summary>
          <div className="tech">
            <Field label={P.toolHeading}>
              <p>{prettyUnits(item.title)}</p>
            </Field>
            <Field label={P.found}>
              <p>{prettyUnits(item.found)}</p>
            </Field>
            <Field label={P.evidence}>
              <ul className="evidence">
                {groupEvidence(item.evidence).map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </Field>
            <Field label={P.rule}>
              <p>{prettyUnits(item.rule)}</p>
            </Field>
            <Field label={P.source}>
              <blockquote>{prettyUnits(item.source)}</blockquote>
            </Field>
            <Field label={P.decides}>
              <p>{prettyUnits(item.scientist_decides)}</p>
            </Field>
          </div>
        </details>
      </div>
    </li>
  );
}

function Checked({ tidy }: { readonly tidy: Tidy }) {
  const list = checkedList(tidy);
  return (
    <section className="block" aria-labelledby="checked">
      <h2 id="checked">{P.checkedTitle}</h2>
      <p className="note">
        {checkedCount(tidy)} things were checked and not flagged. {P.checkedNote}
        {list.length === 0 && checkedCount(tidy) > 0 && ` ${P.checkedCountOnly}`}
      </p>
      <div className="tablewrap">
        <table className="dtable stack">
          <thead>
            <tr>
              <th scope="col">Check</th>
              <th scope="col">What it looked at</th>
              <th scope="col">Items raised</th>
            </tr>
          </thead>
          <tbody>
            {tidy.checks.map((c) => (
              <tr key={c.check}>
                <th scope="row" data-label="Check">
                  <div>
                    {checkName(c.check)}
                    <small className="prov">{c.check}</small>
                  </div>
                </th>
                <td data-label="What it looked at">{c.what_was_checked}</td>
                <td data-label="Items raised">
                  {c.review_items.length === 0
                    ? 'none'
                    : c.review_items.map((n, k) => (
                        <span key={n}>
                          {k > 0 && ', '}
                          <a href={`#item-${n}`}>item {n}</a>
                        </span>
                      ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {list.length > 0 && (
        <details className="drawer">
          <summary>Everything checked and not flagged ({list.length})</summary>
          <ul className="plainlist">
            {list.map((c) => (
              <li key={c.what}>
                {prettyUnits(c.what)} <small className="muted">({checkName(c.check)})</small>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function TidyBody({ tidy }: { readonly tidy: Tidy }) {
  return (
    <>
      <p className="synth">
        <span aria-hidden="true">&#9679;</span> {tidy.synthetic}
      </p>
      <Stats tidy={tidy} />
      <Files tidy={tidy} />
      <section className="block" aria-labelledby="items">
        <h2 id="items">
          {tidy.review_items.length} {P.itemsTitle}
        </h2>
        <p className="note">{P.itemsNote}</p>
        <ol className="ritems">
          {tidy.review_items.map((item) => (
            <Item key={item.number} item={item} />
          ))}
        </ol>
      </section>
      <Checked tidy={tidy} />
      <section className="block" aria-labelledby="notchecked">
        <h2 id="notchecked">{P.notCheckedTitle}</h2>
        <p className="note">{P.notCheckedNote}</p>
        <ul className="plainlist">
          {tidy.not_checked.map((n) => (
            <li key={n.what}>
              <b>{n.what}.</b> {n.reason}
            </li>
          ))}
        </ul>
      </section>
      <section className="block" aria-labelledby="notes">
        <h2 id="notes">{P.notesTitle}</h2>
        <ul className="plainlist">
          {tidy.notes.map((note) => (
            <li key={note}>{prettyUnits(note)}</li>
          ))}
        </ul>
        <details className="drawer">
          <summary>{P.summaryTitle}</summary>
          <p className="note">{prettyUnits(tidy.summary)}</p>
        </details>
      </section>
      <p className="boundary">{P.boundary}</p>
      <div className="linkrow">
        <Link className="btn ghost sm" to="/sources">
          {P.sourcesLink}
        </Link>
        <a className="btn ghost sm" href="/data/tidy.json">
          {P.rawLink}
        </a>
      </div>
    </>
  );
}

export function TidyPage() {
  const loaded = useJson('tidy.json', isTidy);
  return (
    <PageShell title={P.title} lead={<p>{P.lead}</p>}>
      <WhenLoaded loaded={loaded}>{(tidy) => <TidyBody tidy={tidy} />}</WhenLoaded>
    </PageShell>
  );
}
