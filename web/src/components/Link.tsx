// An in-site link that navigates without a page reload (see lib/router.ts).

import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from 'react';
import { navigate } from '../lib/router';

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & { to: string; children: ReactNode };

/** An in-site link: a real <a href>, so it works without JavaScript and opens in a new tab normally. */
export function Link({ to, onClick, children, ...rest }: LinkProps) {
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    if (rest.target !== undefined && rest.target !== '_self') return;
    event.preventDefault();
    navigate(to);
  };
  return (
    <a href={to} onClick={handleClick} {...rest}>
      {children}
    </a>
  );
}
