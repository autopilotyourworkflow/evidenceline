// A tap-to-explain word. Hover shows the explanation, a click or tap pins it, Escape or a click elsewhere closes it.
// An open explanation is nudged sideways so it never runs off the screen (a word near a phone's right edge).

import { useId, useLayoutEffect, useRef, type MouseEvent } from 'react';
import { GLOSSARY } from '../content/landing';
import type { GlossaryKey } from '../content/types';
import { hover, togglePin, useTermOpen } from '../lib/glossary';

/** Space kept between an explanation and the edge of the screen, in CSS pixels. */
const EDGE_GAP = 8;

/** Sets --tip-shift so the explanation box sits inside the viewport; 0 when it already fits. */
function keepOnScreen(tip: HTMLElement): void {
  tip.style.setProperty('--tip-shift', '0px');
  const box = tip.getBoundingClientRect();
  const width = document.documentElement.clientWidth;
  let shift = 0;
  if (box.right > width - EDGE_GAP) shift = width - EDGE_GAP - box.right;
  if (box.left + shift < EDGE_GAP) shift = EDGE_GAP - box.left;
  tip.style.setProperty('--tip-shift', `${Math.round(shift)}px`);
}

export function Term({ name }: { readonly name: GlossaryKey }) {
  const id = useId();
  const { open, pinned } = useTermOpen(id);
  const tip = useRef<HTMLSpanElement | null>(null);
  const entry = GLOSSARY[name];

  // Measured before paint, so an explanation never shows (or widens the page) in the wrong place.
  useLayoutEffect(() => {
    const element = tip.current;
    if (!open || element === null) return;
    const place = () => keepOnScreen(element);
    place();
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [open]);

  const onClick = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    togglePin(id);
  };
  return (
    <button
      className="term"
      type="button"
      aria-expanded={open}
      data-pinned={pinned ? '1' : undefined}
      onClick={onClick}
      onMouseEnter={() => hover(id, true)}
      onMouseLeave={() => hover(id, false)}
      onBlur={() => hover(id, false)}
    >
      {entry.label}
      <span className="tip" role="tooltip" ref={tip}>
        {entry.explanation}
      </span>
    </button>
  );
}
