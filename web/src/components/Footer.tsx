import { BRAND, FOOTER } from '../content/site';

export function Footer() {
  return (
    <footer>
      <div className="wrap">
        <span>
          <b>{BRAND.name}</b>
          {FOOTER.lead}
        </span>
        <span>
          {FOOTER.tail}
          <a className="credits" href={FOOTER.creditsHref}>
            {FOOTER.creditsLabel}
          </a>
          .
        </span>
      </div>
    </footer>
  );
}
