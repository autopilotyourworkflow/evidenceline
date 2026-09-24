// Small previews of the tool's own screens, one per everyday job. They are pictures of a screen, not controls.

import type { ReactNode } from 'react';
import { Icon } from '../components/Icon';
import { Link } from '../components/Link';
import { RichText } from '../components/RichText';
import { ASK_PREVIEW, CHECK_PREVIEW, TIDY_PREVIEW } from '../content/landing';
import type { PreviewId } from '../content/types';
import { BRAND } from '../content/site';
import { isTidy, useJson, type Loaded, type Tidy } from '../lib/data';
import { capitalise, numberWord } from '../lib/format';
import { plainLine } from '../lib/tidyPlain';

function AppFrame({ label, screen, bodyClass, children }: { label: string; screen: string; bodyClass?: string; children: ReactNode }) {
  return (
    <div className="app" role="img" aria-label={label}>
      <div className="app-bar">
        <span className="app-name">{BRAND.name}</span>
        <span className="app-sep" />
        <span className="app-screen">{screen}</span>
      </div>
      <div className={bodyClass ? `app-body ${bodyClass}` : 'app-body'}>{children}</div>
    </div>
  );
}

/** The items found by the real tool (tidy.json), in short plain words. */
function TidyIssues({ loaded }: { readonly loaded: Loaded<Tidy> }) {
  if (loaded.state === 'loading') return null;
  if (loaded.state === 'error') return <div className="app-result">{TIDY_PREVIEW.unavailable}</div>;
  const items = loaded.data.review_items;
  return (
    <>
      <div className="app-result" data-count={items.length}>
        <b>{TIDY_PREVIEW.result}</b> {TIDY_PREVIEW.resultTail(items.length)}
      </div>
      <ul className="issues">
        {items.map((item) => (
          <li key={item.number}>
            <span className="flagchip">Check</span>
            <span>{plainLine(item).short}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

/** "Three files have", "One file has": the start of the preview's description, from the real file count. */
function filesHave(n: number): string {
  const word = capitalise(numberWord(n));
  return n === 1 ? `${word} file has` : `${word} files have`;
}

/** The files the real tool read (tidy.json), in the order it lists them. */
function TidyFiles({ tidy }: { readonly tidy: Tidy }) {
  return (
    <>
      <div className="app-label">Files read</div>
      <ul className="filelist">
        {tidy.files.map((f) => (
          <li key={f.file}>
            <Icon name="file" />
            <span>
              {TIDY_PREVIEW.fileNames[f.role] ?? f.role}
              <small>{f.file}</small>
            </span>
            <em>Read</em>
          </li>
        ))}
      </ul>
    </>
  );
}

function TidyPreview() {
  const loaded = useJson('tidy.json', isTidy);
  const label =
    loaded.state === 'ready'
      ? TIDY_PREVIEW.label(filesHave(loaded.data.files.length), loaded.data.review_items.length)
      : TIDY_PREVIEW.loadingLabel;
  return (
    <>
      <AppFrame label={label} screen={TIDY_PREVIEW.screen}>
        {loaded.state === 'ready' && <TidyFiles tidy={loaded.data} />}
        <TidyIssues loaded={loaded} />
      </AppFrame>
      <p className="preview-note">
        {TIDY_PREVIEW.note} <Link to="/tidy">{TIDY_PREVIEW.fullLink}</Link>.
      </p>
    </>
  );
}

function AskPreview() {
  return (
    <>
      <AppFrame label={ASK_PREVIEW.label} screen={ASK_PREVIEW.screen}>
        <div className="searchbar">
          <Icon name="search" />
          <span className="q">{ASK_PREVIEW.question}</span>
          <span className="go">Ask</span>
        </div>
        <div className="answer-card">
          <div className="app-label">Answer</div>
          <p>
            <RichText text={ASK_PREVIEW.answer} />
          </p>
          <p className="note">{ASK_PREVIEW.answerNote}</p>
        </div>
        <div className="app-label">Sources</div>
        <ul className="sources">
          {ASK_PREVIEW.sources.map((s) => (
            <li key={s.name}>
              <Icon name="file" />
              <span>
                <b>{s.name}</b>
                {s.rest}
              </span>
              <em>Open page</em>
            </li>
          ))}
        </ul>
      </AppFrame>
      <p className="preview-note">
        Preview of the screen. <a href="#try">Ask a question yourself below</a>.
      </p>
    </>
  );
}

function CheckPreview() {
  return (
    <>
      <AppFrame label={CHECK_PREVIEW.label} screen={CHECK_PREVIEW.screen} bodyClass="doccheck">
        <div className="docpane">
          <p>
            <span className="bad">{CHECK_PREVIEW.claim}</span>
            {CHECK_PREVIEW.claimRest}
          </p>
          <p className="muted">
            <RichText text={CHECK_PREVIEW.numbers} />
          </p>
        </div>
        <div className="bubble">
          <b>{CHECK_PREVIEW.bubbleTitle}</b>
          <p>{CHECK_PREVIEW.bubbleText}</p>
          <div className="cbtns">
            <span className="mbtn primary">{CHECK_PREVIEW.acceptLabel}</span>
            <span className="mbtn">{CHECK_PREVIEW.keepMine}</span>
          </div>
        </div>
      </AppFrame>
      <a className="more-link" href="#how">
        {CHECK_PREVIEW.moreLink} <Icon name="arrowDown" size={14} />
      </a>
    </>
  );
}

export function Preview({ id }: { readonly id: PreviewId }) {
  if (id === 'tidy') return <TidyPreview />;
  if (id === 'ask') return <AskPreview />;
  return <CheckPreview />;
}

