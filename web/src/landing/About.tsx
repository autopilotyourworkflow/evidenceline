import { RichText } from '../components/RichText';
import { ABOUT } from '../content/landing';
import { Link } from '../components/Link';
import { CONFIG } from '../lib/config';

export function About() {
  return (
    <section className="about sec alt" id="about" aria-labelledby="abouth">
      <div className="wrap">
        <h2 id="abouth">{ABOUT.title}</h2>
        <div className="aboutgrid">
          <div>
            {ABOUT.paragraphs.map((text, k) => (
              <p key={k}>
                <RichText text={text} />
              </p>
            ))}
          </div>
          <div className="under">
            <h3>{ABOUT.underTitle}</h3>
            <ul>
              {ABOUT.under.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <h3>{ABOUT.scientistsTitle}</h3>
            <ul>
              {ABOUT.forScientists.map((link) => (
                <li key={link.to}>
                  <Link to={link.to}>{link.label}</Link>
                </li>
              ))}
            </ul>
            <div className="linkrow">
              {CONFIG.githubUrl !== null && (
                <a className="btn ghost sm" href={CONFIG.githubUrl} id="github">
                  {ABOUT.codeLabel}
                </a>
              )}
              <a className="btn ghost sm" href={ABOUT.portfolioHref}>
                {ABOUT.portfolioLabel}
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
