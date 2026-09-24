import { Icon } from '../components/Icon';
import { FLOW, FLOW_TITLE } from '../content/landing';

/** The four steps, on a white card lifted over the lower edge of the hero photo. */
export function FlowCard() {
  return (
    <div className="flowband">
      <div className="wrap">
        <div className="flowcard">
          <h2>{FLOW_TITLE}</h2>
          <ol className="flow4">
            {FLOW.map((step) => (
              <li key={step.title}>
                <span className="ic">
                  <Icon name={step.icon} />
                </span>
                <b>{step.title}</b>
                <span>{step.text}</span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}
