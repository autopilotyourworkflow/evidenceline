// "Try it yourself": suggested questions with answers prepared in advance (answers.json), a question box that asks
// the live service when one is configured (VITE_API_BASE), and the connector link when one exists (VITE_MCP_URL).
// Until they are switched on, the box and the card say they are coming soon; with no answers.json the two
// hand-written examples are shown. With VITE_TURNSTILE_SITE_KEY set, a live question carries a Turnstile token.
// After a question the page brings the loading line or the answer into view (and the robot check, when it asks for a
// tick), and a short status line tells screen readers what happened instead of reading the whole answer out.

import { useEffect, useRef, useState, type FormEvent, type ReactNode, type Ref } from 'react';
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
  // check: still waiting for the "not a robot" check's token; false once the question has been sent.
  | { readonly kind: 'loading'; readonly check: boolean }
  | { readonly kind: 'live'; readonly view: AnswerView; readonly question: string }
  | { readonly kind: 'rate-limited'; readonly retryAfter: number | null; readonly daily: boolean }
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
  if (minutes === 60) return ' in about an hour';
  if (minutes < 90) return ` in about ${minutes} minutes`;
  return ` in about ${Math.ceil(minutes / 60)} hours`;
}

/** A message that already tells the reader what to do next. */
const SAYS_NEXT_STEP = /paused|try again|reload/i;

/** A message from the service, then what to do next, without repeating what the message already says. */
function withNextStep(message: string, next: string): string {
  if (message === '') return next;
  return SAYS_NEXT_STEP.test(message) ? `${message} ${TRY.stillWorks}` : `${message} ${next}`;
}

/** The loading line: waiting for the robot check (and asking for the tick when it wants one), then for the answer. */
const loadingText = (check: boolean, ticking: boolean): string => (!check ? TRY.loading : ticking ? TRY.tickToSend : TRY.checking);

/** The text of the states that are only a message; '' for the others. */
function messageOf(shown: Shown): string {
  switch (shown.kind) {
    case 'offline':
      return TRY.comingSoonMessage;
    case 'robot-waiting':
      return TRY.robotWaiting;
    case 'robot-unavailable':
      return TRY.robotUnavailable;
    case 'rate-limited':
      return (shown.daily ? TRY.dailyLimit : TRY.rateLimited)(waitText(shown.retryAfter));
    case 'paused':
      return withNextStep(shown.message, TRY.paused);
    case 'error':
      return withNextStep(shown.message, TRY.errorTail);
    default:
      return '';
  }
}

/** The short line screen readers hear: what is happening, or that an answer is ready (not the whole answer). */
function statusOf(shown: Shown, prepared: readonly PreparedAnswer[], ticking: boolean): string {
  switch (shown.kind) {
    case 'none':
      return '';
    case 'prepared':
      return TRY.answerReady(prepared[shown.index]?.question ?? '');
    case 'fallback':
      return TRY.answerReady(EXAMPLE_ANSWERS[shown.index]?.question ?? '');
    case 'live':
      return TRY.answerReady(shown.question);
    case 'loading':
      return loadingText(shown.check, ticking);
    default:
      return messageOf(shown);
  }
}

/** What the answer area holds, and its data-state; null while it is empty. */
function answerBody(shown: Shown, prepared: readonly PreparedAnswer[], ticking: boolean, onAsk: (question: string) => void): { state: string; content: ReactNode } | null {
  switch (shown.kind) {
    case 'none':
      return null;
    case 'prepared': {
      const entry = prepared[shown.index];
      if (entry === undefined) return null;
      return { state: 'prepared', content: <AnswerBody view={entry.view} note={preparedNote(entry.prepared)} onAsk={onAsk} /> };
    }
    case 'fallback': {
      const answer = EXAMPLE_ANSWERS[shown.index];
      if (answer === undefined) return null;
      return {
        state: 'fallback',
        content: (
          <>
            {answer.paragraphs.map((text, k) => (
              <p key={k}>
                <RichText text={text} />
              </p>
            ))}
            <p className="src">{answer.source}</p>
          </>
        ),
      };
    }
    case 'live':
      return { state: 'live', content: <AnswerBody view={shown.view} note={TRY.liveNote(shown.view.kind, shown.view.citations.length > 0)} onAsk={onAsk} /> };
    case 'loading':
      return {
        state: 'loading',
        content: (
          <p className="loading">
            <span className="pending-dot" aria-hidden="true" />
            {loadingText(shown.check, ticking)}
          </p>
        ),
      };
    default:
      return { state: shown.kind, content: <p>{messageOf(shown)}</p> };
  }
}

type AnswerProps = {
  readonly shown: Shown;
  readonly prepared: readonly PreparedAnswer[];
  readonly ticking: boolean;
  readonly onAsk: (question: string) => void;
  readonly answerRef: Ref<HTMLDivElement>;
};

function Answer({ shown, prepared, ticking, onAsk, answerRef }: AnswerProps) {
  const body = answerBody(shown, prepared, ticking, onAsk);
  // The same element in every state. tabIndex -1: the page moves focus here when the button that asked is gone (a
  // suggested question) or disabled (Ask, while the question is on its way).
  return (
    <div
      className="answer"
      id="answer"
      ref={answerRef}
      tabIndex={-1}
      style={body === null ? undefined : { display: 'block' }}
      data-state={body?.state}
      data-phase={shown.kind === 'loading' ? (!shown.check ? 'answer' : ticking ? 'tick' : 'check') : undefined}
      aria-busy={shown.kind === 'loading' ? 'true' : undefined}
    >
      {body?.content}
    </div>
  );
}

/** How long a live question waits for the "not a robot" check to give a token before it gives up. */
const ROBOT_WAIT_MS = 15_000;
/** How long in all it waits when the check asks the visitor to tick a box: the question is sent with the tick. */
const ROBOT_TICK_WAIT_MS = 120_000;
/** The longest question the box takes (the live service takes the same). */
const MAX_QUESTION = 500;

/** Lower case, with punctuation, hyphens and extra spaces left out, so near-identical questions match. */
const normal = (s: string): string =>
  s.toLowerCase().replace(/\p{Pd}/gu, ' ').replace(/[^\p{L}\p{N}\s]/gu, '').replace(/\s+/g, ' ').trim();
const same = (a: string, b: string): boolean => normal(a) === normal(b);

/**
 * What to bring into view after a question: from the question box (or, when that is too tall for the screen, from
 * just under the box, where the robot check shows) or from the answer, and which end wins when even that is too tall.
 */
type View = { readonly from: 'question' | 'answer'; readonly keep: 'top' | 'bottom' };

/** Space kept between what is brought into view and the edge of the screen, or the sticky bar at the top. */
const EDGE = 12;

/**
 * Scrolls as little as possible to bring a stretch of the page into view below the sticky bar, from the first of
 * `tops` that fits on one screen with `bottom` (screen coordinates; the space kept at the foot may be given up). When
 * none fits, the last one is used and `keep` says which end stays in view. Smooth, unless the reader asked for less
 * motion.
 */
function bringIntoView(tops: readonly number[], bottom: number, keep: 'top' | 'bottom'): void {
  const first = Math.max(0, document.querySelector('.bar')?.getBoundingClientRect().bottom ?? 0) + EDGE;
  const last = window.innerHeight - EDGE;
  const top = tops.find((t) => bottom - t <= window.innerHeight - first) ?? tops.at(-1) ?? bottom;
  const by = bottom - top > last - first ? (keep === 'top' ? top - first : bottom - last) : top < first ? top - first : Math.max(0, bottom - last);
  if (Math.abs(by) < 1) return;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  window.scrollBy({ top: by, behavior: reduced ? 'auto' : 'smooth' });
}

function AskBox() {
  const loaded = useJson('answers.json', isAnswersFile);
  // Every prepared answer, lookup-only ones included, answers a question asked in the box or from a suggestion; the
  // first six of the others are offered as suggested questions.
  const all = loaded.state === 'ready' ? readAnswersFile(loaded.data) : [];
  const chips = all.flatMap((p, index) => (p.chip ? [{ p, index }] : [])).slice(0, 6);
  const meta = loaded.state === 'ready' ? readAnswersFileMeta(loaded.data) : null;
  const usePrepared = chips.length > 0;
  const [question, setQuestion] = useState('');
  const [shown, setShown] = useState<Shown>({ kind: 'none' });
  const inflight = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement | null>(null);
  const askForm = useRef<HTMLFormElement | null>(null);
  const answer = useRef<HTMLDivElement | null>(null);
  /** Set by a question or a click, used once by the effect below. */
  const wantedView = useRef<View | null>(null);
  const focusAnswer = useRef(false);
  const wasLoading = useRef(false);
  /** A question asked before answers.json arrived: asked once it has, so a prepared one is never sent live. */
  const early = useRef<{ readonly text: string; readonly view: View } | null>(null);
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
  /** A question is waiting for the visitor to tick the robot check's box. */
  const ticking = shown.kind === 'loading' && shown.check && robotInteractive;

  useEffect(() => () => inflight.current?.abort(), []);

  // After each change, bring into view what the visitor needs: the loading line or the start of the answer, and the
  // robot check while it waits for a tick. A result that arrives after the visitor scrolled away is left there.
  useEffect(() => {
    const wanted = wantedView.current;
    wantedView.current = null;
    const arrived = wasLoading.current && shown.kind !== 'loading';
    wasLoading.current = shown.kind === 'loading';
    const box = answer.current;
    const form = askForm.current;
    if (box === null || form === null) return;
    const area = box.getBoundingClientRect();
    const onScreen = area.bottom > 0 && area.top < window.innerHeight;
    const view: View | null = ticking ? { from: 'question', keep: 'top' } : (wanted ?? (arrived && onScreen ? { from: 'answer', keep: 'top' } : null));
    if (view !== null) {
      // Of an answer, up to half a screen: enough to see that it came and to start reading.
      const end = Math.min(area.bottom, area.top + window.innerHeight / 2);
      const question = form.getBoundingClientRect();
      bringIntoView(view.from === 'question' ? [question.top, question.bottom] : [area.top], end, view.keep);
    }
    if (focusAnswer.current) {
      focusAnswer.current = false;
      box.focus({ preventScroll: true });
    }
  }, [shown, ticking]);

  /** Answers a question: from the prepared copy when it was prepared in advance, otherwise from the live service. */
  const ask = (text: string, view: View) => {
    const q = text.trim();
    if (q === '') return;
    inflight.current?.abort();
    wantedView.current = view;
    // Until answers.json has arrived it is not known whether the question was prepared, so it waits for the file.
    if (loaded.state === 'loading') {
      early.current = { text: q, view };
      setShown({ kind: 'loading', check: false });
      return;
    }
    // A question that was prepared in advance is answered from the prepared copy: no live call, no cost.
    const match = all.findIndex((p) => same(p.question, q));
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
    // Until the robot check has a token the loading line says so, and asks for the tick when Cloudflare wants one.
    const check = robotOn && robotState !== 'ready';
    setShown({ kind: 'loading', check });
    void (async () => {
      let token: string | null = null;
      if (robotOn) {
        token = await robotToken(ROBOT_WAIT_MS, ROBOT_TICK_WAIT_MS);
        if (controller.signal.aborted) return;
        if (token === null) {
          inflight.current = null;
          setShown({ kind: robotUnavailable() ? 'robot-unavailable' : 'robot-waiting' });
          return;
        }
        if (check) setShown({ kind: 'loading', check: false });
      }
      const outcome = await askLive(apiBase, q, controller.signal, token);
      // A token is good for one question: ask the check for a fresh one whatever the answer was.
      if (token !== null) spendRobot();
      if (controller.signal.aborted) return;
      inflight.current = null;
      if (outcome.kind === 'answer') setShown({ kind: 'live', view: outcome.view, question: q });
      else setShown(outcome);
    })();
  };

  // The question that waited for answers.json, once the file has arrived (or failed to).
  useEffect(() => {
    if (loaded.state === 'loading' || early.current === null) return;
    const { text, view } = early.current;
    early.current = null;
    ask(text, view);
  });

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (question.trim() === '') {
      input.current?.focus();
      return;
    }
    // Focus moves to the answer when it would otherwise be lost (Ask is disabled while the question is on its way), and
    // on a touch screen, where the keyboard would cover the answer (moving focus closes it).
    if (document.activeElement !== input.current || window.matchMedia('(pointer: coarse)').matches) focusAnswer.current = true;
    ask(question, { from: 'question', keep: 'bottom' });
  };

  /**
   * A suggested question from an answer: put it in the box and ask it, with the box in view and focus on the answer.
   * On a screen too short for both, the loading line or the new answer wins over the box.
   */
  const askSuggested = (text: string) => {
    setQuestion(text);
    focusAnswer.current = true;
    ask(text, { from: 'question', keep: 'bottom' });
  };

  const choose = (text: string, next: Shown) => {
    inflight.current?.abort();
    wantedView.current = { from: 'answer', keep: 'top' };
    setQuestion(text);
    setShown(next);
  };

  const when = meta === null ? '' : preparedWhen(meta);
  const atLimit = question.length >= MAX_QUESTION;

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
      <form className="ask" id="askf" ref={askForm} onSubmit={onSubmit}>
        <label className="skip" htmlFor="q">
          {TRY.inputLabel}
        </label>
        <input
          id="q"
          ref={input}
          placeholder={TRY.placeholder}
          autoComplete="off"
          maxLength={MAX_QUESTION}
          aria-describedby={atLimit ? 'qlimit' : undefined}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button className="btn primary sm" type="submit" disabled={shown.kind === 'loading'}>
          Ask
        </button>
      </form>
      {atLimit && (
        <p className="smallprint" id="qlimit" role="status">
          {TRY.questionLimit(MAX_QUESTION)}
        </p>
      )}
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
              ? chips.map(({ p, index }, k) => (
                  <button key={p.question} type="button" data-a={k} onClick={() => choose(p.question, { kind: 'prepared', index })}>
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
      <p className="skip" id="answer-status" role="status">
        {statusOf(shown, all, ticking)}
      </p>
      <Answer shown={shown} prepared={all} ticking={ticking} onAsk={askSuggested} answerRef={answer} />
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

/** How long the Copy button says "Copied" before it can be used again. */
const COPIED_MS = 2500;

function ConnectBox() {
  const [copyState, setCopyState] = useState<CopyState>('idle');
  const address = useRef<HTMLElement | null>(null);
  const copiedTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(copiedTimer.current), []);
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
  // "Copied" only once the clipboard confirms it, and only for a moment. When the clipboard is missing or refuses
  // (insecure context, denied permission), the address is selected instead and a note says so.
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
    clipboard.writeText(url).then(() => {
      setCopyState('copied');
      window.clearTimeout(copiedTimer.current);
      copiedTimer.current = window.setTimeout(() => setCopyState('idle'), COPIED_MS);
    }, failed);
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
              {copyState === 'copied' ? TRY.copyDoneNote : copyState === 'failed' ? TRY.copyFailedNote : ''}
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
