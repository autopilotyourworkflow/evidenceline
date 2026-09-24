// Renders a run of typed inline copy (see content/types.ts).

import { Fragment } from 'react';
import type { Inline, Rich } from '../content/types';
import { Link } from './Link';
import { Term } from './Term';

function renderInline(part: Inline, key: number) {
  if (typeof part === 'string') return <Fragment key={key}>{part}</Fragment>;
  if ('b' in part) return <b key={key}>{part.b}</b>;
  if ('em' in part) return <em key={key}>{part.em}</em>;
  if ('term' in part) return <Term key={key} name={part.term} />;
  if ('fill' in part) return <Fill key={key} value={part.fill} />;
  if ('to' in part)
    return (
      <Link key={key} to={part.to}>
        {part.text}
      </Link>
    );
  return (
    <a key={key} href={part.href} target="_blank" rel="noopener">
      {part.text}
    </a>
  );
}

export function RichText({ text }: { readonly text: Rich }) {
  return <>{text.map(renderInline)}</>;
}

/** A number copied in by code: shown with the sand highlight, and never broken across lines. */
export function Fill({ value }: { readonly value: string }) {
  return <span className="fill">{value}</span>;
}
