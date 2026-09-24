// The sticky brown bar. On the landing page the menu links jump to sections; elsewhere they lead back to them.

import { BRAND } from '../content/site';
import { NAV } from '../content/landing';
import { Link } from './Link';

export function Header({ onLanding }: { readonly onLanding: boolean }) {
  return (
    <header className="bar">
      <div className="wrap">
        <Link className="brand" to="/">
          {BRAND.name}
          <span>{BRAND.tag}</span>
        </Link>
        <nav className="nav" aria-label="Main">
          {NAV.map((item) =>
            onLanding ? (
              <a key={item.href} className={item.cta ? 'cta' : undefined} href={item.href}>
                {item.label}
              </a>
            ) : (
              <Link key={item.href} className={item.cta ? 'cta' : undefined} to={`/${item.href}`}>
                {item.label}
              </Link>
            ),
          )}
        </nav>
      </div>
    </header>
  );
}
