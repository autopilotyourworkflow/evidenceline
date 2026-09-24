// Step 5: the scientist uses the suggested sentence or keeps their own with a reason, and the choice is logged.
// The walkthrough remounts this component (a new React key) on "Start again", which clears the decision.

import { useEffect, useRef, useState } from 'react';
import { Icon } from '../components/Icon';
import { RichText } from '../components/RichText';
import { DRAFT_TAIL, ORIGINAL_SENTENCE, STEP_VISUALS as V, SUGGESTED_SENTENCE } from '../content/landing';
import { DocTitle } from './StepVisuals';

type Decision =
  | { readonly kind: 'pending'; readonly askingReason: boolean }
  | { readonly kind: 'accepted'; readonly log: string }
  | { readonly kind: 'kept'; readonly log: string };

const stamp = (): string =>
  new Date().toLocaleString('en-AU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

export function DecisionPanel() {
  const [decision, setDecision] = useState<Decision>({ kind: 'pending', askingReason: false });
  const [reason, setReason] = useState('');
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const askingReason = decision.kind === 'pending' && decision.askingReason;

  useEffect(() => {
    if (askingReason) reasonRef.current?.focus();
  }, [askingReason]);

  const accept = () =>
    setDecision({
      kind: 'accepted',
      log: `Recorded: suggested sentence used, ${stamp()}. On the real system this entry carries the scientist's name.`,
    });

  const save = () => {
    const text = reason.trim();
    if (text === '') {
      reasonRef.current?.focus();
      return;
    }
    setDecision({ kind: 'kept', log: `Recorded: original sentence kept. Reason: "${text}", ${stamp()}.` });
  };

  const sentenceClass = decision.kind === 'pending' ? 'bad' : decision.kind === 'accepted' ? 'good' : '';
  const log = decision.kind === 'pending' ? '' : decision.log;

  return (
    <>
      <div className="doc">
        <DocTitle state={decision.kind === 'pending' ? V.waitingState : V.recordedState} stateId="docstate" />
        <p>
          <span id="s1" className={sentenceClass}>
            {decision.kind === 'accepted' ? SUGGESTED_SENTENCE : ORIGINAL_SENTENCE}
          </span>
          <RichText text={DRAFT_TAIL} />
        </p>
      </div>
      <div className="flag" id="suggest" style={{ borderColor: 'var(--hair)', display: decision.kind === 'pending' ? undefined : 'none' }}>
        <span className="dot" style={{ background: 'var(--sand)' }}>
          <Icon name="pencil" />
        </span>
        <div>
          <b style={{ color: 'var(--brown)' }}>{V.suggestTitle}</b>
          <p id="sugg">{SUGGESTED_SENTENCE}</p>
          <div className="choice">
            <button className="btn primary sm" id="accept" type="button" onClick={accept}>
              {V.accept}
            </button>
            <button className="btn ghost sm" id="keep" type="button" onClick={() => setDecision({ kind: 'pending', askingReason: true })}>
              {V.keep}
            </button>
          </div>
          <div className="reason" id="reason" style={askingReason ? { display: 'block' } : undefined}>
            <label htmlFor="rtext">{V.reasonLabel}</label>
            <textarea id="rtext" ref={reasonRef} value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="choice">
              <button className="btn primary sm" id="save" type="button" onClick={save}>
                {V.save}
              </button>
            </div>
          </div>
        </div>
      </div>
      <p className="log" id="log" role="status" style={log === '' ? undefined : { display: 'block' }}>
        {log}
      </p>
    </>
  );
}
