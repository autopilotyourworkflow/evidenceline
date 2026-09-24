// /accuracy: the published test results (accuracy.json), failures shown as plainly as passes.
// Until the test run publishes results the file holds a placeholder, and the page says results will appear.

import { Fragment } from 'react';
import { Link } from '../components/Link';
import { PageShell, WhenLoaded } from '../components/PageShell';
import { ACCURACY_PAGE as P } from '../content/pages';
import { isAccuracyFile, METRIC_NAMES, readAccuracy, type AccuracyView, type CheckedNote, type SearchSet, type TestArea, type Verdict } from '../lib/accuracy';
import { useJson } from '../lib/data';
import { formatDate, prettyUnits } from '../lib/format';

const plural = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`;

/** A result in words with a symbol, so it never depends on colour alone. */
function Status({ ok, text }: { readonly ok: boolean; readonly text: string }) {
  return (
    <span className={ok ? 'st ok' : 'st no'}>
      <span aria-hidden="true">{ok ? '✓' : '✗'}</span> {text}
    </span>
  );
}

const agreesInEvery = (v: { readonly verdicts: readonly Verdict[] }): boolean => v.verdicts.every((d) => d.agrees);

/** A note stands when every re-check confirmed its current wording, or, with no re-check, when every pass agreed. */
const noteStands = (n: CheckedNote): boolean => n.confirmedNow || (n.rechecks.length === 0 && agreesInEvery(n));

/** "The right page came first for 6 of 24 questions", from the set's hit@1 count, when it has one. */
function firstPlace(set: SearchSet): string | null {
  const hit1 = set.metrics.find((m) => m.name === 'hit@1' || m.name === 'hit_at_1');
  if (hit1?.hits === undefined || hit1.total === undefined) return null;
  return `${set.label}: the right page came first for ${hit1.hits} of ${plural(hit1.total, 'question', 'questions')}.`;
}

/** The same set's first-place score with its near copies of tuning questions left out, when the file gives it. */
function leftOutLine(set: SearchSet): string | null {
  const strict = set.withoutNearCopies;
  if (strict === null) return null;
  const hit1 = strict.metrics.find((m) => m.name === 'hit@1' || m.name === 'hit_at_1');
  if (hit1?.hits === undefined || hit1.total === undefined) return null;
  const n = strict.leftOut.length;
  return `Leaving out the ${plural(n, 'question', 'questions')} close in wording to a tuning question, the right page came first for ${hit1.hits} of ${hit1.total}.`;
}

function InShort({ view }: { readonly view: AccuracyView }) {
  const passed = view.tests.reduce((n, t) => n + t.passed, 0);
  const failed = view.tests.reduce((n, t) => n + t.failed, 0);
  const skipped = view.tests.reduce((n, t) => n + t.skipped, 0);
  const agreed = view.values.filter(agreesInEvery).length;
  const lines: string[] = [];
  if (view.tests.length > 0) {
    const fails = failed > 0 ? `; ${plural(failed, 'test', 'tests')} failed` : '';
    const skips = skipped > 0 ? ` (${skipped} skipped)` : '';
    lines.push(`${passed} of ${passed + failed} automated tests passed${fails}${skips}.`);
  }
  if (view.values.length > 0) {
    const off = view.values.length - agreed;
    const offText = off > 0 ? `; ${off} did not and ${off === 1 ? 'is' : 'are'} listed below` : '';
    lines.push(`${agreed} of ${plural(view.values.length, 'guideline value', 'guideline values')} matched the source page in every pass${offText}.`);
  }
  const rechecked = view.notes.filter((n) => n.confirmedNow).length;
  const openNotes = view.notes.filter((n) => !noteStands(n)).length;
  if (rechecked > 0) {
    lines.push(`${plural(rechecked, 'explanatory note', 'explanatory notes')} next to the values ${rechecked === 1 ? 'was' : 'were'} reworded to follow the source after the first passes, and the later re-checks confirmed the new wording.`);
  }
  if (openNotes > 0) {
    lines.push(`${plural(openNotes, 'explanatory note', 'explanatory notes')} next to the values ${openNotes === 1 ? 'is' : 'are'} not confirmed and ${openNotes === 1 ? 'is' : 'are'} listed below.`);
  }
  const openFindings = view.findings.filter((f) => f.open).length;
  if (openFindings > 0) {
    lines.push(`${plural(openFindings, 'other re-check finding is', 'other re-check findings are')} still open and listed below.`);
  }
  for (const set of view.search.filter((s) => s.partOf === '')) {
    const line = firstPlace(set);
    if (line !== null) lines.push(line);
    const strict = leftOutLine(set);
    if (strict !== null) lines.push(strict);
  }
  return (
    <div className="callout" data-results="true">
      <h2>{P.inShortTitle}</h2>
      {lines.map((line) => (
        <p key={line}>{line}</p>
      ))}
      <p className="note">
        {view.lastRun !== null ? `Last run: ${formatDate(view.lastRun)}` : 'Date of the run not recorded'}
        {view.build !== null ? `, ${view.build}.` : '.'}
      </p>
    </div>
  );
}

/** "all passed" only when nothing failed and nothing was skipped: a skipped test did not pass. */
function resultText(t: TestArea): string {
  if (t.failed > 0) return plural(t.failed, 'failure', 'failures');
  return t.skipped > 0 ? `no failures, ${t.skipped} skipped` : 'all passed';
}

function Tests({ view }: { readonly view: AccuracyView }) {
  if (view.tests.length === 0) return null;
  const hasSkipped = view.tests.some((t) => t.skipped > 0);
  return (
    <section className="block" aria-labelledby="tests">
      <h2 id="tests">{P.testsTitle}</h2>
      <p className="note">{P.testsNote}</p>
      <div className="tablewrap">
        <table className="dtable stack">
          <thead>
            <tr>
              <th scope="col">Area</th>
              <th scope="col">Passed</th>
              <th scope="col">Failed</th>
              {hasSkipped && <th scope="col">Skipped</th>}
              <th scope="col">Result</th>
            </tr>
          </thead>
          <tbody>
            {view.tests.map((t) => (
              <tr key={t.name} className={t.failed > 0 ? 'failrow' : undefined}>
                <th scope="row" data-label="Area">
                  <div>
                    {t.name}
                    {t.failures.length > 0 && <small className="prov">{t.failures.join(', ')}</small>}
                  </div>
                </th>
                <td className="num" data-label="Passed">
                  {t.passed}
                </td>
                <td className="num" data-label="Failed">
                  {t.failed}
                </td>
                {hasSkipped && (
                  <td className="num" data-label="Skipped">
                    {t.skipped}
                  </td>
                )}
                <td data-label="Result">
                  <Status ok={t.failed === 0} text={resultText(t)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Search({ view }: { readonly view: AccuracyView }) {
  if (view.search.length === 0) return null;
  return (
    <section className="block" aria-labelledby="search">
      <h2 id="search">{P.searchTitle}</h2>
      <p className="note">{P.searchNote}</p>
      <div className="rules">
        {view.search.map((set) => (
          <div className="rulecard" key={set.id}>
            <h3>{set.label}</h3>
            {set.questions !== null && <p className="note">{plural(set.questions, 'question', 'questions')}</p>}
            {set.note !== '' && <p className="note">{set.note}</p>}
            <ul className="limits">
              {set.metrics.map((m) => (
                <li key={m.name}>
                  <div className="limit-head">
                    <b>{METRIC_NAMES[m.name] ?? m.name}</b>
                    <span className="v">{m.shown}</span>
                  </div>
                  <span className="scenario">{m.name}</span>
                </li>
              ))}
            </ul>
            {set.misses.length > 0 && (
              <details className="misses">
                <summary>
                  {P.missesLabel} ({set.misses.length})
                </summary>
                <ul className="plainlist">
                  {set.misses.map((m) => (
                    <li key={`${m.id}-${m.question}`}>
                      {m.question}
                      {m.result !== '' && <small className="prov">{m.result}</small>}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function VerdictCell({ verdict }: { readonly verdict: Verdict | undefined }) {
  if (verdict === undefined) return <span className="muted">not run</span>;
  return (
    <div>
      <Status ok={verdict.agrees} text={verdict.verdict} />
      {verdict.note !== '' && <small className="prov">{verdict.note}</small>}
    </div>
  );
}

function Verification({ view }: { readonly view: AccuracyView }) {
  if (view.values.length === 0) return null;
  const passIds = view.passes.length > 0 ? view.passes.map((p) => p.id) : [...new Set(view.values.flatMap((v) => v.verdicts.map((d) => d.pass)))];
  return (
    <section className="block" aria-labelledby="verify">
      <h2 id="verify">{P.verifyTitle}</h2>
      <p className="note">{P.verifyNote}</p>
      {view.passes.length + view.rechecks.length > 0 && (
        <dl className="facts passes">
          {view.passes.map((p) => (
            <Fragment key={p.id}>
              <dt>Pass {p.id}</dt>
              <dd>{[p.label, p.method].filter((x) => x !== '').join(': ') || 'No description recorded.'}</dd>
            </Fragment>
          ))}
          {view.rechecks.map((p) => (
            <Fragment key={`re-${p.id}`}>
              <dt>Re-check {p.id}</dt>
              <dd>{[p.label, p.method].filter((x) => x !== '').join(': ') || 'No description recorded.'}</dd>
            </Fragment>
          ))}
        </dl>
      )}
      <div className="tablewrap">
        <table className="dtable stack">
          <thead>
            <tr>
              <th scope="col">Guideline value</th>
              <th scope="col">Source</th>
              {passIds.map((id) => (
                <th scope="col" key={id}>
                  Pass {id}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.values.map((v) => (
              <tr key={v.id} className={agreesInEvery(v) ? undefined : 'failrow'}>
                <th scope="row" data-label="Guideline value">
                  <div>
                    {v.label}
                    {v.value !== '' && (
                      <span className="v">
                        {': '}
                        {v.value} {prettyUnits(v.unit)}
                      </span>
                    )}
                    {v.note !== '' && <small className="prov">{v.note}</small>}
                  </div>
                </th>
                <td data-label="Source">{v.source === '' ? <span className="muted">not recorded</span> : v.source}</td>
                {passIds.map((id) => (
                  <td key={id} data-label={`Pass ${id}`}>
                    <VerdictCell verdict={v.verdicts.find((d) => d.pass === id)} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Notes view={view} passIds={passIds} />
      <Findings view={view} />
    </section>
  );
}

/** Re-check ids in the order the file lists the re-checks, else in the order the verdicts name them. */
function recheckIds(view: AccuracyView, verdicts: readonly (readonly Verdict[])[]): string[] {
  if (view.rechecks.length > 0) return view.rechecks.map((r) => r.id);
  return [...new Set(verdicts.flatMap((list) => list.map((d) => d.pass)))];
}

function Findings({ view }: { readonly view: AccuracyView }) {
  if (view.findings.length === 0) return null;
  const ids = recheckIds(view, view.findings.map((f) => f.verdicts));
  return (
    <>
      <h3 id="findings">{P.findingsTitle}</h3>
      <p className="note">{P.findingsNote}</p>
      <div className="tablewrap">
        <table className="dtable stack">
          <thead>
            <tr>
              <th scope="col">Finding</th>
              {ids.map((id) => (
                <th scope="col" key={id}>
                  Re-check {id}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.findings.map((f) => (
              <tr key={f.id} className={f.open ? 'failrow' : undefined}>
                <th scope="row" data-label="Finding">
                  <div>
                    {prettyUnits(f.label)}
                    {f.context !== '' && <small className="prov">{f.context}</small>}
                    {f.resolution !== '' && <small className="prov">{f.resolution}</small>}
                  </div>
                </th>
                {ids.map((id) => (
                  <td key={id} data-label={`Re-check ${id}`}>
                    <VerdictCell verdict={f.verdicts.find((d) => d.pass === id)} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Notes({ view, passIds }: { readonly view: AccuracyView; readonly passIds: readonly string[] }) {
  if (view.notes.length === 0) return null;
  const reIds = view.notes.some((n) => n.rechecks.length > 0) ? recheckIds(view, view.notes.map((n) => n.rechecks)) : [];
  return (
    <>
      <h3 id="notes">{P.notesTitle}</h3>
      <p className="note">{P.notesNote}</p>
      <div className="tablewrap">
        <table className="dtable stack notes">
          <thead>
            <tr>
              <th scope="col">Note</th>
              {passIds.map((id) => (
                <th scope="col" key={id}>
                  Pass {id}
                </th>
              ))}
              {reIds.map((id) => (
                <th scope="col" key={`re-${id}`}>
                  Re-check {id}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.notes.map((n) => (
              <tr key={n.id} className={noteStands(n) ? undefined : 'failrow'}>
                <th scope="row" data-label="Note">
                  <div>
                    {n.label}
                    {n.fileNote !== '' && (
                      <small className="prov">
                        {n.checkedNote === '' ? 'The file says' : 'The file now says'}: "{prettyUnits(n.fileNote)}"
                      </small>
                    )}
                    {n.checkedNote !== '' && (
                      <small className="prov">The first two passes checked this earlier wording: "{prettyUnits(n.checkedNote)}"</small>
                    )}
                    {n.sourceSays !== '' && <small className="prov">The source says: "{prettyUnits(n.sourceSays)}"</small>}
                    {n.status !== '' && <small className="prov">{n.status}</small>}
                  </div>
                </th>
                {passIds.map((id) => (
                  <td key={id} data-label={`Pass ${id}`}>
                    <VerdictCell verdict={n.verdicts.find((d) => d.pass === id)} />
                  </td>
                ))}
                {reIds.map((id) => (
                  <td key={`re-${id}`} data-label={`Re-check ${id}`}>
                    <VerdictCell verdict={n.rechecks.find((d) => d.pass === id)} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function AccuracyBody({ raw }: { readonly raw: Record<string, unknown> }) {
  const view = readAccuracy(raw);
  return (
    <>
      {view === null || !view.hasResults ? (
        <section className="block" aria-labelledby="results">
          <h2 id="results">{P.resultsTitle}</h2>
          <div className="pending" role="status">
            <span className="pending-dot" aria-hidden="true" />
            <p>{view?.message ?? 'Results will appear here when the automated tests run.'}</p>
          </div>
        </section>
      ) : (
        <>
          <InShort view={view} />
          <Tests view={view} />
          <Search view={view} />
          <Verification view={view} />
        </>
      )}
      <section className="block" aria-labelledby="method">
        <h2 id="method">{P.methodTitle}</h2>
        <p>{P.method}</p>
        <p>
          <Link to="/sources">{P.sourcesLink}</Link>
        </p>
      </section>
      <section className="block" aria-labelledby="scope">
        <h2 id="scope">{P.notCheckedTitle}</h2>
        <p className="note">{P.notCheckedNote}</p>
        <ul className="plainlist">
          {P.notChecked.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>
    </>
  );
}

export function AccuracyPage() {
  const loaded = useJson('accuracy.json', isAccuracyFile);
  return (
    <PageShell title={P.title} lead={<p>{P.lead}</p>}>
      <WhenLoaded loaded={loaded}>{(raw) => <AccuracyBody raw={raw} />}</WhenLoaded>
    </PageShell>
  );
}
