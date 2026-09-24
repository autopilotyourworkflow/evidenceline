// A tiny history router: five static pages do not need a routing library.
// Hash links such as "#try" keep their normal in-page behaviour; "/#try" from another page goes home, then scrolls.

import { useEffect, useRef, useSyncExternalStore } from 'react';

export type RouteId = 'home' | 'sample-site' | 'tidy' | 'sources' | 'accuracy' | 'not-found';

export const ROUTE_PATHS = {
  home: '/',
  'sample-site': '/sample-site',
  tidy: '/tidy',
  sources: '/sources',
  accuracy: '/accuracy',
} as const satisfies Record<Exclude<RouteId, 'not-found'>, string>;

const NAVIGATE_EVENT = 'evidenceline:navigate';

function normalise(pathname: string): string {
  const trimmed = pathname.replace(/\/+$/, '');
  return trimmed === '' ? '/' : trimmed;
}

export function routeFor(pathname: string): RouteId {
  const path = normalise(pathname);
  for (const [id, routePath] of Object.entries(ROUTE_PATHS)) {
    if (routePath === path) return id as RouteId;
  }
  return 'not-found';
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener('popstate', onChange);
  window.addEventListener(NAVIGATE_EVENT, onChange);
  return () => {
    window.removeEventListener('popstate', onChange);
    window.removeEventListener(NAVIGATE_EVENT, onChange);
  };
}

const snapshot = (): string => window.location.pathname + window.location.hash;

/** The current path and hash, re-rendering on navigation. */
export function useLocation(): { pathname: string; hash: string } {
  const value = useSyncExternalStore(subscribe, snapshot, () => '/');
  const hashAt = value.indexOf('#');
  return hashAt === -1 ? { pathname: value, hash: '' } : { pathname: value.slice(0, hashAt), hash: value.slice(hashAt) };
}

export function navigate(to: string): void {
  window.history.pushState(null, '', to);
  window.dispatchEvent(new Event(NAVIGATE_EVENT));
}

/**
 * On first load and after moving to another page: jump to the hash target if there is one, otherwise to the top.
 * A hash change within the same page is left to the browser, so in-page links keep their smooth scrolling.
 */
export function useScrollOnNavigate(pathname: string, hash: string): void {
  const previousPath = useRef<string | null>(null);
  useEffect(() => {
    const pageChanged = previousPath.current !== pathname;
    previousPath.current = pathname;
    if (!pageChanged) return;
    const id = hash.length > 1 ? decodeURIComponent(hash.slice(1)) : '';
    const target = id === '' ? null : document.getElementById(id);
    if (!target) {
      window.scrollTo({ top: 0, behavior: 'instant' });
      return;
    }
    target.scrollIntoView({ behavior: 'instant' });
    return keepInView(target);
  }, [pathname, hash]);
}

/**
 * Sections above the target can grow once their data files load (the tidy preview, the suggested questions), which
 * would push a deep-linked section down. For a short while, re-align the target when the page changes size, and stop
 * as soon as the reader scrolls, taps or types.
 */
function keepInView(target: HTMLElement, forMs = 2000): () => void {
  let active = true;
  const stop = () => {
    active = false;
    observer.disconnect();
    for (const type of ['wheel', 'touchstart', 'keydown', 'mousedown'] as const) window.removeEventListener(type, stop);
  };
  const observer = new ResizeObserver(() => {
    if (active) target.scrollIntoView({ behavior: 'instant' });
  });
  observer.observe(document.body);
  for (const type of ['wheel', 'touchstart', 'keydown', 'mousedown'] as const) window.addEventListener(type, stop, { passive: true });
  const timer = window.setTimeout(stop, forMs);
  return () => {
    window.clearTimeout(timer);
    stop();
  };
}
