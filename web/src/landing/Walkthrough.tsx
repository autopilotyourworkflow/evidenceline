// "See it work: one well, five steps". All five panes stay mounted (only one is shown), so the step-5 decision
// survives moving between steps; "Start again" goes back to step 1 and clears it.

import { useState, type ReactNode } from 'react';
import { RichText } from '../components/RichText';
import { TechDetail } from '../components/TechDetail';
import { STEPS, TOUR_INTRO } from '../content/landing';
import { DecisionPanel } from './Decision';
import { CheckedDoc, DraftDoc, ResultsChart, RuleRulers } from './StepVisuals';

const LAST = STEPS.length - 1;

function visualFor(step: number, resetKey: number): ReactNode {
  switch (step) {
    case 0:
      return <ResultsChart />;
    case 1:
      return <DraftDoc />;
    case 2:
      return <CheckedDoc />;
    case 3:
      return <RuleRulers />;
    default:
      return <DecisionPanel key={resetKey} />;
  }
}

export function Walkthrough() {
  const [current, setCurrent] = useState(0);
  const [resetKey, setResetKey] = useState(0);
  const go = (n: number) => setCurrent(Math.max(0, Math.min(LAST, n)));
  const onNext = () => {
    if (current === LAST) {
      setResetKey((k) => k + 1);
      go(0);
    } else {
      go(current + 1);
    }
  };

  return (
    <section className="tour" id="how" aria-labelledby="tourh">
      <div className="wrap">
        <h2 id="tourh">{TOUR_INTRO.title}</h2>
        <p className="lead">{TOUR_INTRO.lead}</p>
        <ol className="steps" id="steps">
          {STEPS.map((step, k) => (
            <li key={step.tab}>
              <button
                type="button"
                data-i={k}
                aria-current={k === current ? 'step' : undefined}
                className={k < current ? 'done' : undefined}
                onClick={() => go(k)}
              >
                <b>{k + 1}</b>
                {step.tab}
              </button>
            </li>
          ))}
        </ol>

        <div className="stage" id="stage" aria-live="polite">
          {STEPS.map((step, k) => (
            <div key={step.tab} className={k === current ? 'pane on' : 'pane'} data-p={k}>
              <div className="visual fade">{visualFor(k, resetKey)}</div>
              <div className="caption fade">
                <span className="num">
                  Step {k + 1} of {STEPS.length}
                </span>
                <h3>{step.title}</h3>
                {step.paragraphs.map((text, j) => (
                  <p key={j}>
                    <RichText text={text} />
                  </p>
                ))}
                <TechDetail blocks={step.tech} />
              </div>
            </div>
          ))}
        </div>
        <div className="nextrow">
          <button
            className="btn ghost sm"
            id="back"
            type="button"
            disabled={current === 0}
            style={current === 0 ? { display: 'none' } : undefined}
            onClick={() => go(current - 1)}
          >
            Back
          </button>
          <button className="btn primary sm" id="next" type="button" onClick={onNext}>
            {current === LAST ? 'Start again' : 'Next step'}
          </button>
          <span className="count" id="count">
            Step {current + 1} of {STEPS.length}
          </span>
        </div>

        <p className="summary">
          <b>{TOUR_INTRO.summaryLead}</b>
          {TOUR_INTRO.summary}
        </p>
      </div>
    </section>
  );
}
