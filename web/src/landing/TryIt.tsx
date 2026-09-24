// "Try it yourself": suggested questions with answers prepared in advance (answers.json), a question box that asks
// the live service when one is configured (VITE_API_BASE), and the connector link when one exists (VITE_MCP_URL).
// Until they are switched on, the box and the card say they are coming soon; with no answers.json the two
// hand-written examples are shown. With VITE_TURNSTILE_SITE_KEY set, a live question carries a Turnstile token.

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { RichText } from '../components/RichText';
import { AnswerBody } from './AnswerCard';
import { EXAMPLE_ANSWERS, TRY } from '../content/landing';
import { askLive, isAnswersFile, readAnswersFile, readAnswersFileMeta, type AnswerView, type Prepared, type PreparedAnswer } from '../lib/answers';
import { CONFIG } from '../lib/config';
import { useJson } from '../lib/data';
import { formatDate } from '../lib/format';
import { useRobotCheck } from '../lib/turnstile';

type Shown =
  | { readonly kind: 'none' }
  | { readonly kind: 'prepared'; readonly index: number }
  | { readonly kind: 'fallback'; readonly index: number }
  | { readonly kind: 'offline' }
  | { readonly kind: 'robot-waiting' }
  | { readonly kind: 'robot-unavailable' }
  | { readonly kind: 'loading' }
  | { readonly kind: 'live'; readonly view: AnswerView }
  | { readonly kind: 'rate-limited'; readonly retryAfter: number | null }
  | { readonly kind: 'paused'; readonly message: string }
  | { readonly kind: 'error'; readonly message: string };

/** " on 24 Sep 2026 with claude-sonnet-5" from what answers.json records; empty when it records neither. */
function preparedWhen(prepared: Prepared): string {
  const date = prepared.date === null ? '' : ` on ${formatDate(prepared.date)}`;
  return `${date}${prepared.model === null ? '' : ` with ${prepared.model}`}`;
}

/** The small print under a prepared answer: the file's own sentence for it when there is one. */
const preparedNote = (prepared: Prepared): string =>
  prepared.label !== null && /[.!]$/.test(prepared.label) ? prepared.label : TRY.preparedNote(preparedWhen(prepared));

/** " in about 3 minutes" from a Retry-After in seconds. */
function waitText(retryAfter: number | null): string {
  if (retryAfter === null) return ' in a minute';
  if (retryAfter < 90) return ' in a minute';
  const minutes = Math.ceil(retryAfter / 60);
  if (minutes < 90) return ` in about ${minutes} minutes`;
  return ` in about ${Math.ceil(minutes / 60)} hours`;
}

function Answer({ shown, prepared, onAsk }: { readonly shown: Shown; readonly prepared: readonly PreparedAnswer[]; readonly onAsk: (question: string) => void }) {
  const open = { display: 'block' } as const;
  switch (shown.kind) {
    case 'none':
      return <div className="answer" id="answer" aria-live="polite" />;
    case 'prepared': {
      const entry = prepared[shown.index];
      if (entry === undefined) return <div className="answer" id="answer" aria-live="polite" />;
      return (
        <div className="answer" id="answer" aria-live="polite" style={open} data-state="prepared">
          <AnswerBody view={entry.view} note={preparedNote(entry.prepared)} onAsk={onAsk} />
        </div>
      );
    }
    case 'fallback': {
      const answer = EXAMPLE_ANSWERS[shown.index];
      if (answer === undefined) return <div className="answer" id="answer" aria-live="polite" />;
      return (
        <div className="answer" id="answer" aria-live="polite" style={open} data-state="fallback">
          {answer.paragraphs.map((text, k) => (
            <p key={k}>
              <RichText text={text} />
            </p>
          ))}
          <p className="src">{answer.source}</p>
        </div>
      );
    }
    case 'live':
      return (
        <div className="answer" id="answer" aria-live="polite" style={open} data-state="live">
          <AnswerBody view={shown.view} note={TRY.liveNote(shown.view.kind, shown.view.citations.length > 0)} onAsk={onAsk} />
        </div>
      );
    case 'loading':
      return (
        <div className="answer" id="answer" aria-live="polite" style={open} data-state="loading" aria-busy="true">
          <p className="loading">
            <span className="pending-dot" aria-hidden="true" />
            {TRY.loading}
          </p>
        </div>
      );
    case 'offline':
    case 'robot-waiting':
    case 'robot-unavailable':
    case 'rate-limited':
    case 'paused':
    case 'error': {
      const message =
        shown.kind === 'offline'
          ? TRY.comingSoonMessage
          : shown.kind === 'robot-waiting'
            ? TRY.robotWaiting
            : shown.kind === 'robot-unavailable'
              ? TRY.robotUnavailable
              : shown.kind === 'rate-limited'
                ? TRY.rateLimited(waitText(shown.retryAfter))
                : shown.kind === 'paused'
                  ? [shown.message, TRY.paused].filter((m) => m !== '').join(' ')
                  : `${shown.message} ${TRY.errorTail}`;
      return (
        <div className="answer" id="answer" aria-live="polite" style={open} data-state={shown.kind}>
          <p>{message}</p>
        </div>
      );
    }
  }
}

/** How long a live question waits for the "not a robot" check to give a token before it gives up. */
const ROBOT_WAIT_MS = 15_000;

const same = (a: string, b: string): boolean => a.trim().toLowerCase().replace(/\s+/g, ' ') === b.trim().toLowerCase().replace(/\s+/g, ' ');

function AskBox() {
  const loaded = useJson('answers.json', isAnswersFile);
  const prepared = loaded.state === 'ready' ? readAnswersFile(loaded.data).slice(0, 6) : [];
  const meta = loaded.state === 'ready' ? readAnswersFileMeta(loaded.data) : null;
  const usePrepared = prepared.length > 0;
  const [question, setQuestion] = useState('');
  const [shown, setShown] = useState<Shown>({ kind: 'none' });
  const inflight = useRef<AbortController | null>(null);
  // The robot check exists only when the live service does: without it there is nothing to protect.
  const {
    enabled: robotOn,
    attach: attachRobot,
    state: robotState,
    interactive: robotInteractive,
    token: robotToken,
    spend: spendRobot,
    unavailable: robotUnavailable,
  } = useRobotCheck(CONFIG.apiBase === null ? null : CONFIG.turnstileSiteKey);

  useEffect(() => () => inflight.current?.abort(), []);

  /** Answers a question: from the prepared copy when it was prepared in advance, otherwise from the live service. */
  const ask = (text: string) => {
    const q = text.trim();
    if (q === '') return;
    inflight.current?.abort();
    // A question that was prepared in advance is answered from the prepared copy: no live call, no cost.
    const match = prepared.findIndex((p) => same(p.question, q));
    if (match !== -1) {
      setShown({ kind: 'prepared', index: match });
      return;
    }
    const apiBase = CONFIG.apiBase;
    if (apiBase === null) {
      setShown({ kind: 'offline' });
      return;
    }
    const controller = new AbortController();
    inflight.current = controller;
    setShown({ kind: 'loading' });
    void (async () => {
      let token: string | null = null;
      if (robotOn) {
        token = await robotToken(ROBOT_WAIT_MS);
        if (controller.signal.aborted) return;
        if (token === null) {
          inflight.current = null;
          setShown({ kind: robotUnavailable() ? 'robot-unavailable' : 'robot-waiting' });
          return;
        }
      }
      const outcome = await askLive(apiBase, q, controller.signal, token);
      // A token is good for one question: ask the check for a fresh one whatever the answer was.
      if (token !== null) spendRobot();
      if (controller.signal.aborted) return;
      inflight.current = null;
      if (outcome.kind === 'answer') setShown({ kind: 'live', view: outcome.view });
      else setShown(outcome);
    })();
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    ask(question);
  };

  /** A suggested question from an answer: put it in the box and ask it. */
  const askSuggested = (text: string) => {
    setQuestion(text);
    ask(text);
  };

  const choose = (text: string, next: Shown) => {
    inflight.current?.abort();
    setQuestion(text);
    setShown(next);
  };

  const when = meta === null ? '' : preparedWhen(meta);

  return (
    <div className="box">
      <h3>{TRY.askTitle}</h3>
      <p>{TRY.askText}</p>
      {CONFIG.apiBase === null && (
        <div className="pending later soon" id="coming-soon" role="note">
          <span className="pending-dot" aria-hidden="true" />
          <p>{TRY.comingSoon}</p>
        </div>
      )}
      <form className="ask" id="askf" onSubmit={onSubmit}>
        <label className="skip" htmlFor="q">
          {TRY.inputLabel}
        </label>
        <input
          id="q"
          placeholder={TRY.placeholder}
          autoComplete="off"
          maxLength={500}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button className="btn primary sm" type="submit" disabled={shown.kind === 'loading'}>
          Ask
        </button>
      </form>
      {robotOn && (
        <div
          className="robot"
          id="robot"
          ref={attachRobot}
          role="group"
          aria-label={TRY.robotLabel}
          data-state={robotState}
          data-interactive={robotInteractive ? 'true' : 'false'}
        />
      )}
      {loaded.state !== 'loading' && (
        <>
          <div className="chips">
            {usePrepared
              ? prepared.map((p, k) => (
                  <button key={p.question} type="button" data-a={k} onClick={() => choose(p.question, { kind: 'prepared', index: k })}>
                    {p.question}
                  </button>
                ))
              : EXAMPLE_ANSWERS.map((answer, k) => (
                  <button key={answer.question} type="button" data-a={k} onClick={() => choose(answer.question, { kind: 'fallback', index: k })}>
                    {answer.question}
                  </button>
                ))}
          </div>
          <p className="smallprint" id="prepared-note">
            {usePrepared ? `${TRY.preparedIntro}${when === '' ? '' : ` Prepared${when}.`}` : TRY.fallbackIntro}
          </p>
        </>
      )}
      <Answer shown={shown} prepared={prepared} onAsk={askSuggested} />
    </div>
  );
}

/** Selects the text of an element, so a reader whose browser refused the clipboard can copy it by hand. */
function selectText(element: HTMLElement | null): void {
  const selection = window.getSelection();
  if (element === null || selection === null) return;
  const range = document.createRange();
  range.selectNodeContents(element);
  selection.removeAllRanges();
  selection.addRange(range);
}

type CopyState = 'idle' | 'copied' | 'failed';

function ConnectBox() {
  const [copyState, setCopyState] = useState<CopyState>('idle');
  const address = useRef<HTMLElement | null>(null);
  const url = CONFIG.mcpUrl;
  if (url === null) {
    return (
      <div className="box" id="connect">
        <h3>{TRY.connectTitle}</h3>
        <p>{TRY.connectTextLater}</p>
        <div className="pending later" role="note">
          <span className="pending-dot" aria-hidden="true" />
          <p>{TRY.connectLater}</p>
        </div>
      </div>
    );
  }
  // "Copied" only once the clipboard confirms it. When the clipboard is missing or refuses (insecure context, denied
  // permission), the address is selected instead and a note says so.
  const copy = () => {
    const failed = () => {
      selectText(address.current);
      setCopyState('failed');
    };
    const clipboard = typeof navigator.clipboard?.writeText === 'function' ? navigator.clipboard : null;
    if (clipboard === null) {
      failed();
      return;
    }
    clipboard.writeText(url).then(() => setCopyState('copied'), failed);
  };
  const label = copyState === 'copied' ? TRY.copyDone : copyState === 'failed' ? TRY.copyFailed : TRY.copyIdle;
  const [first, ...rest] = TRY.connectSteps;
  return (
    <div className="box" id="connect">
      <h3>{TRY.connectTitle}</h3>
      <p>{TRY.connectText}</p>
      <ol className="connect">
        <li>
          <div>
            {first}
            <div className="url">
              <code id="mcp" ref={address}>
                {url}
              </code>
              <button className="btn ghost sm" id="copy" type="button" onClick={copy}>
                {label}
              </button>
            </div>
            <p className="smallprint copynote" role="status">
              {copyState === 'failed' ? TRY.copyFailedNote : ''}
            </p>
          </div>
        </li>
        {rest.map((step) => (
          <li key={step}>
            <div>{step}</div>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function TryIt() {
  return (
    <section className="sec" id="try" aria-labelledby="tryh">
      <div className="wrap">
        <h2 id="tryh">{TRY.title}</h2>
        <p className="lead" style={{ marginBottom: 0 }}>
          {TRY.lead(CONFIG.apiBase !== null, CONFIG.mcpUrl !== null)}
        </p>
        <div className="try">
          <AskBox />
          <ConnectBox />
        </div>
      </div>
    </section>
  );
}
