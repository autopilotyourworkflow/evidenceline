import { Icon } from '../components/Icon';
import { PILLARS, TRUST } from '../content/landing';

export function TrustBand() {
  return (
    <section className="band" id="trust" aria-labelledby="trusth">
      <div className="wrap">
        <h2 id="trusth">{TRUST.title}</h2>
        <p className="lead">{TRUST.lead}</p>
        <ul className="pillars">
          {PILLARS.map((pillar) => (
            <li key={pillar.title}>
              <span className="ic">
                <Icon name={pillar.icon} />
              </span>
              <b>{pillar.title}</b>
              <span>{pillar.text}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
