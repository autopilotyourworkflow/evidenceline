// Line icons from the design, drawn inline so they take the text colour. Every icon is decorative (aria-hidden).

import type { ReactElement } from 'react';
import type { IconName } from '../content/types';

type Spec = { readonly size: number; readonly box: string; readonly stroke?: string; readonly width?: number; readonly body: ReactElement };

const round = { strokeLinecap: 'round', strokeLinejoin: 'round' } as const;

const ICONS: Readonly<Record<IconName, Spec>> = {
  flask: {
    size: 26,
    box: '0 0 26 26',
    body: (
      <g {...round}>
        <path d="M10 3h6M11 3v7l-6 11a2 2 0 0 0 1.8 3h12.4a2 2 0 0 0 1.8-3l-6-11V3" />
        <path d="M8 17h10" />
      </g>
    ),
  },
  draft: {
    size: 26,
    box: '0 0 26 26',
    body: (
      <g {...round}>
        <path d="M6 3h10l5 5v15H6z" />
        <path d="M16 3v5h5M9.5 13h8M9.5 17h8M9.5 21h5" />
      </g>
    ),
  },
  check: {
    size: 26,
    box: '0 0 26 26',
    body: (
      <g {...round}>
        <circle cx="11" cy="11" r="7" />
        <path d="M16 16l6 6M8 11l2.2 2.2L14.5 9" />
      </g>
    ),
  },
  person: {
    size: 26,
    box: '0 0 26 26',
    body: (
      <g {...round}>
        <circle cx="13" cy="8" r="4" />
        <path d="M5 23c0-4.4 3.6-8 8-8s8 3.6 8 8" />
      </g>
    ),
  },
  numbers: {
    size: 28,
    box: '0 0 28 28',
    body: (
      <>
        <path d="M5 7h18M5 14h18M5 21h11" />
        <path d="M19 19l2.5 2.5L26 17" />
      </>
    ),
  },
  scientist: {
    size: 28,
    box: '0 0 28 28',
    body: (
      <>
        <circle cx="14" cy="9" r="4.5" />
        <path d="M5.5 25c0-4.7 3.8-8.5 8.5-8.5s8.5 3.8 8.5 8.5" />
      </>
    ),
  },
  lock: {
    size: 28,
    box: '0 0 28 28',
    body: (
      <>
        <rect x="6" y="12" width="16" height="12" rx="1.5" />
        <path d="M9.5 12V9a4.5 4.5 0 0 1 9 0v3" />
      </>
    ),
  },
  chart: {
    size: 28,
    box: '0 0 28 28',
    body: (
      <>
        <path d="M5 23V5M5 23h18" />
        <path d="M9 17l4-5 4 3 5-7" />
      </>
    ),
  },
  file: { size: 16, box: '0 0 16 18', stroke: '#887447', width: 1.3, body: <path d="M2 1h8l4 4v12H2zM10 1v4h4" /> },
  search: {
    size: 18,
    box: '0 0 18 18',
    stroke: '#483725',
    body: (
      <>
        <circle cx="7.5" cy="7.5" r="5.5" />
        <path d="M11.5 11.5L16 16" />
      </>
    ),
  },
  arrowDown: { size: 16, box: '0 0 16 16', body: <path d="M8 2v11M3 8.5l5 5 5-5" /> },
  chevron: { size: 12, box: '0 0 12 12', body: <path d="M4 2l4 4-4 4" /> },
  cross: { size: 14, box: '0 0 14 14', stroke: '#fff', width: 1.8, body: <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" /> },
  tick: { size: 14, box: '0 0 14 14', stroke: '#fff', width: 1.8, body: <path d="M3 7.5l2.5 2.5L11 4.5" /> },
  pencil: { size: 14, box: '0 0 14 14', stroke: '#483725', width: 1.4, body: <path d="M2 10.5l7-7 2 2-7 7H2z" /> },
};

type IconProps = { readonly name: IconName; readonly size?: number };

export function Icon({ name, size }: IconProps) {
  const spec = ICONS[name];
  const [, , boxW = '16', boxH = '16'] = spec.box.split(' ');
  const w = size ?? spec.size;
  const h = Math.round((w * Number(boxH)) / Number(boxW));
  return (
    <svg width={w} height={h} viewBox={spec.box} aria-hidden="true" fill="none" stroke={spec.stroke ?? 'currentColor'} strokeWidth={spec.width ?? 1.6}>
      {spec.body}
    </svg>
  );
}
