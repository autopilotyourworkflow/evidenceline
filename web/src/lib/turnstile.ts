// Cloudflare Turnstile, the "not a robot" check in front of the live question box. It is used only when the build
// sets VITE_TURNSTILE_SITE_KEY and the live service is on; the script is fetched from challenges.cloudflare.com only
// then, and only once the question box is near the screen. The API checks the token (TURNSTILE_SECRET on the service).
//
// Which kind of check a visitor sees (managed, non-interactive or invisible) is a setting of the site key in the
// Cloudflare dashboard, not of this code. Evidenceline's key is a managed one.

import { useCallback, useEffect, useRef, useState } from 'react';

/** Cloudflare's script, with explicit rendering and a ready callback, as its documentation shows. */
export const TURNSTILE_SCRIPT = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
const READY_CALLBACK = '__evidencelineTurnstileReady';

/** The render options this page uses (a subset of Cloudflare's). */
type TurnstileOptions = {
  readonly sitekey: string;
  readonly action: string;
  readonly theme: 'auto' | 'light' | 'dark';
  readonly size: 'normal' | 'flexible' | 'compact';
  readonly callback: (token: string) => void;
  readonly 'expired-callback': () => void;
  readonly 'timeout-callback': () => void;
  readonly 'error-callback': (code: string) => void;
};

type TurnstileApi = {
  render(container: HTMLElement, options: TurnstileOptions): string | null | undefined;
  reset(widgetId: string): void;
  remove(widgetId: string): void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
    __evidencelineTurnstileReady?: () => void;
  }
}

/** How long the page waits for Cloudflare's script before calling the check unavailable. */
const SCRIPT_TIMEOUT_MS = 20_000;

let loading: Promise<TurnstileApi> | null = null;

/** Loads Cloudflare's script once per page. Rejects when it cannot be loaded (blocked, offline or too slow). */
export function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile !== undefined) return Promise.resolve(window.turnstile);
  if (loading !== null) return loading;
  loading = new Promise<TurnstileApi>((resolve, reject) => {
    const fail = () => {
      loading = null;
      reject(new Error('Turnstile could not be loaded'));
    };
    const timer = window.setTimeout(fail, SCRIPT_TIMEOUT_MS);
    window[READY_CALLBACK] = () => {
      window.clearTimeout(timer);
      if (window.turnstile === undefined) fail();
      else resolve(window.turnstile);
    };
    const script = document.createElement('script');
    script.src = `${TURNSTILE_SCRIPT}?render=explicit&onload=${READY_CALLBACK}`;
    script.async = true;
    script.addEventListener('error', () => {
      window.clearTimeout(timer);
      fail();
    });
    document.head.append(script);
  });
  return loading;
}

/** idle: not loaded yet; checking: loaded, no token yet; ready: a token is waiting; unavailable: it could not load. */
export type RobotCheckState = 'idle' | 'checking' | 'ready' | 'unavailable';

export type RobotCheck = {
  /** Whether this build has the check at all (a site key is set). */
  readonly enabled: boolean;
  /** Callback ref for the element the widget goes in. */
  readonly attach: (element: HTMLDivElement | null) => void;
  readonly state: RobotCheckState;
  /** The current token, waiting up to `timeoutMs` for one. Null when none arrives or the check is unavailable. */
  readonly token: (timeoutMs: number) => Promise<string | null>;
  /** Marks the token as used (each one is good for one question) and asks the widget for a fresh one. */
  readonly spend: () => void;
  /** Whether the check cannot give a token right now (script blocked, offline, or a widget error). */
  readonly unavailable: () => boolean;
};

const NO_CHECK: RobotCheck = {
  enabled: false,
  attach: () => undefined,
  state: 'idle',
  token: () => Promise.resolve(null),
  spend: () => undefined,
  unavailable: () => false,
};

/**
 * The Turnstile widget for the question box. With no site key it does nothing at all: no script, no element, no
 * token. With one, the script is loaded when the widget's element comes within about a screen of the viewport.
 */
export function useRobotCheck(siteKey: string | null): RobotCheck {
  const [element, attach] = useState<HTMLDivElement | null>(null);
  const [state, setState] = useState<RobotCheckState>('idle');
  const current = useRef<string | null>(null);
  /** True while the check cannot give a token (script blocked or widget error), so nobody waits for one. */
  const broken = useRef(false);
  const widget = useRef<{ api: TurnstileApi; id: string } | null>(null);
  const waiters = useRef<((token: string | null) => void)[]>([]);

  const settle = useCallback((token: string | null) => {
    const pending = waiters.current;
    waiters.current = [];
    for (const resolve of pending) resolve(token);
  }, []);

  useEffect(() => {
    if (siteKey === null || element === null) return undefined;
    let cancelled = false;

    const start = () => {
      setState('checking');
      loadTurnstile().then(
        (api) => {
          if (cancelled) return;
          const id = api.render(element, {
            sitekey: siteKey,
            action: 'ask',
            theme: 'light',
            size: 'flexible',
            callback: (token) => {
              current.current = token;
              broken.current = false;
              setState('ready');
              settle(token);
            },
            // Cloudflare refreshes an expired token by itself; until then there is none to send.
            'expired-callback': () => {
              current.current = null;
              setState('checking');
            },
            'timeout-callback': () => {
              current.current = null;
              setState('checking');
            },
            'error-callback': () => {
              current.current = null;
              broken.current = true;
              setState('unavailable');
              settle(null);
            },
          });
          if (typeof id === 'string') widget.current = { api, id };
        },
        () => {
          if (cancelled) return;
          broken.current = true;
          setState('unavailable');
          settle(null);
        },
      );
    };

    let observer: IntersectionObserver | null = null;
    if (typeof IntersectionObserver === 'function') {
      observer = new IntersectionObserver(
        (entries) => {
          if (!entries.some((e) => e.isIntersecting)) return;
          observer?.disconnect();
          start();
        },
        { rootMargin: '600px 0px' },
      );
      observer.observe(element);
    } else {
      start();
    }

    return () => {
      cancelled = true;
      observer?.disconnect();
      if (widget.current !== null) widget.current.api.remove(widget.current.id);
      widget.current = null;
      current.current = null;
      settle(null);
    };
  }, [siteKey, element, settle]);

  const token = useCallback(
    (timeoutMs: number) => {
      if (current.current !== null) return Promise.resolve(current.current);
      if (broken.current) return Promise.resolve(null);
      return new Promise<string | null>((resolve) => {
        const timer = window.setTimeout(() => {
          waiters.current = waiters.current.filter((w) => w !== done);
          resolve(null);
        }, timeoutMs);
        const done = (value: string | null) => {
          window.clearTimeout(timer);
          resolve(value);
        };
        waiters.current.push(done);
      });
    },
    [],
  );

  const spend = useCallback(() => {
    current.current = null;
    if (widget.current === null) return;
    setState('checking');
    widget.current.api.reset(widget.current.id);
  }, []);

  const unavailable = useCallback(() => broken.current, []);

  if (siteKey === null) return NO_CHECK;
  return { enabled: true, attach, state, token, spend, unavailable };
}
