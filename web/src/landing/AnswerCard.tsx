// One result from the answering pipeline, prepared or live: plain answer first, then the guideline values, the
// sources with page links, and (one click away) the other passages and the checks the answer passed.

import type { ReactNode } from 'react';
import { Icon } from '../components/Icon';
import { TRY } from '../content/landing';
import type { AnswerView, Citation } from '../lib/answers';
import { prettyUnits } from '../lib/format';

/** "page 29 (PDF page 34)", or null for a web page with no page numbers. */
function pageText(c: Citation): string | null {
  if (c.printedPage !== '' && c.pdfPage !== '') return `page ${c.printedPage} (PDF page ${c.pdfPage})`;
  if (c.printedPage !== '') return `page ${c.printedPage}`;
  if (c.pdfPage !== '') return `PDF page ${c.pdfPage}`;
  return null;
}

function CitationItem({ c }: { readonly c: Citation }) {
  const page = pageText(c);
  const inferred = c.location.includes('printed page inferred') ? ' (printed page inferred)' : '';
  const where = page === null ? (c.section !== '' ? `section "${c.section}", web page` : 'web page') : c.section !== '' ? c.section : '';
  const linkText = page === null ? 'Open the web page' : `Open ${page}${inferred}`;
  return (
    <li>
      <span className="cn" aria-hidden="true">
        {c.n}
      </span>
      <div>
        <span className="skip">Source {c.n}: </span>
        <b>{c.document}</b>
        {c.edition !== '' && ` (${c.edition})`}
        {where !== '' && `, ${where}`}
        {'. '}
        {c.link === null ? page ?? '' : (
          <a href={c.link} target="_blank" rel="noopener">
            {linkText}
          </a>
        )}
        {c.excerpt !== '' && <small className="excerpt">{c.excerpt}</small>}
        {c.notice !== '' && <small className="lic">{c.notice}</small>}
      </div>
    </li>
  );
}

function CitationList({ items }: { readonly items: readonly Citation[] }) {
  return (
    <ol className="cites">
      {items.map((c) => (
        <CitationItem key={`${c.n}-${c.link ?? c.document}`} c={c} />
      ))}
    </ol>
  );
}

function More({ label, children }: { readonly label: string; readonly children: ReactNode }) {
  return (
    <details className="more">
      <summary>
        <Icon name="chevron" />
        {label}
      </summary>
      {children}
    </details>
  );
}

/** Cited passages as "Sources"; with none cited, the first three passages, and the rest one click away. */
function Passages({ citations }: { readonly citations: readonly Citation[] }) {
  if (citations.length === 0) return null;
  const cited = citations.filter((c) => c.cited === true);
  const anyCitedFlag = citations.some((c) => c.cited !== null);
  if (cited.length > 0 || !anyCitedFlag) {
    const shown = cited.length > 0 ? cited : citations;
    const rest = cited.length > 0 ? citations.filter((c) => c.cited !== true) : [];
    return (
      <>
        <p className="alabel">{TRY.sourcesTitle}</p>
        <CitationList items={shown} />
        {rest.length > 0 && (
          <More label={TRY.otherPassages(rest.length)}>
            <CitationList items={rest} />
          </More>
        )}
      </>
    );
  }
  const head = citations.slice(0, 3);
  const tail = citations.slice(3);
  return (
    <>
      <p className="alabel">{TRY.passagesTitle}</p>
      <CitationList items={head} />
      {tail.length > 0 && (
        <More label={TRY.otherPassages(tail.length)}>
          <CitationList items={tail} />
        </More>
      )}
    </>
  );
}

const paragraphs = (text: string): string[] => text.split(/\n\s*\n/).filter((p) => p.trim() !== '');

export function AnswerBody({ view, note }: { readonly view: AnswerView; readonly note: string }) {
  // A checked answer and the fixed reply about Evidenceline itself need no heading.
  const title = view.kind === 'answered' || view.kind === 'about' ? null : TRY.titles[view.kind];
  // The main text: the answer when there is one; otherwise the pipeline's own explanation of what happened.
  const main =
    view.answer !== ''
      ? view.answer
      : view.explanation !== ''
        ? view.explanation
        : view.kind === 'not-covered'
          ? TRY.notCoveredDefault
          : view.kind === 'guard-rail'
            ? TRY.guardRailDefault
            : '';
  const explanationShownAsMain = view.answer === '' && view.explanation !== '';
  return (
    <>
      {title !== null && <p className="akind">{title}</p>}
      {paragraphs(main).map((para) => (
        <p key={para}>{prettyUnits(para)}</p>
      ))}
      {view.values.length > 0 && (
        <>
          <p className="alabel">{TRY.valuesTitle}</p>
          <ul className="gvals">
            {view.values.map((v) => (
              <li key={`${v.marker}-${v.text}`}>
                {v.marker !== '' && <span className="marker">{v.marker}</span>}
                {prettyUnits(v.text)}
                {v.source !== '' && <small>{v.source}</small>}
                {v.note !== '' && <small>{prettyUnits(v.note)}</small>}
              </li>
            ))}
          </ul>
        </>
      )}
      <Passages citations={view.citations} />
      {view.redactions > 0 && <p className="src">{TRY.redacted(view.redactions)}</p>}
      {view.notes.map((n) => (
        <p className="src" key={n}>
          {n}
        </p>
      ))}
      {view.kind !== 'about' && (view.checks.length > 0 || (!explanationShownAsMain && view.explanation !== '')) && (
        <More label={TRY.checksTitle}>
          {!explanationShownAsMain && view.explanation !== '' && <p className="src">{view.explanation}</p>}
          {view.checks.length > 0 && (
            <ul className="checks">
              {view.checks.map((c) => (
                <li key={c.name}>
                  <span className={c.passed ? 'st ok' : 'st no'}>
                    <span aria-hidden="true">{c.passed ? '✓' : '✗'}</span> {c.passed ? 'passed' : 'failed'}
                  </span>{' '}
                  <b>{c.name}</b>
                  {c.detail !== '' && `: ${c.detail}`}
                </li>
              ))}
            </ul>
          )}
          {view.checkSummary !== '' && view.checks.length === 0 && <p className="src">{view.checkSummary}</p>}
        </More>
      )}
      <p className="src prepared">{note}</p>
    </>
  );
}
