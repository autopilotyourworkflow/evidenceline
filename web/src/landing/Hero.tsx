import { Icon } from '../components/Icon';
import { HERO } from '../content/landing';

export function Hero() {
  return (
    <section className="phero" aria-labelledby="h1">
      <div className="wrap">
        <h1 id="h1">{HERO.title}</h1>
        <p className="sub">{HERO.sub}</p>
        <div className="btns">
          <a className="btn primary" href="#how">
            {HERO.primary} <Icon name="arrowDown" />
          </a>
          <a className="btn light" href="#try">
            {HERO.secondary}
          </a>
        </div>
      </div>
      <div className="credit">
        <div className="wrap">{HERO.credit}</div>
      </div>
    </section>
  );
}
