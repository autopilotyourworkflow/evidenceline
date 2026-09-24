// The left-hand picture for walkthrough steps 1 to 4. Step 5's picture is the decision (Decision.tsx).

import type { CSSProperties } from 'react';
import { Icon } from '../components/Icon';
import { RichText } from '../components/RichText';
import { DRAFT_TAIL, FLAGGED_CLAIM, MB2_PFOS_CHART, ORIGINAL_SENTENCE, STEP_VISUALS as V } from '../content/landing';

export function ResultsChart() {
  return (
    <>
      <p className="vt">
        <RichText text={V.chartTitle} />
      </p>
      <div className="chart" aria-hidden="true">
        {MB2_PFOS_CHART.map((bar, k) => (
          <div className="col" key={bar.label}>
            <span className="val">{bar.value}</span>
            <span className="barv" style={{ height: `${bar.height}%`, animationDelay: k === 0 ? undefined : `${k * 60}ms` }} />
          </div>
        ))}
      </div>
      <div className="xl" aria-hidden="true">
        {MB2_PFOS_CHART.map((bar) => (
          <span key={bar.label}>{bar.label}</span>
        ))}
      </div>
      <table className="skip">
        <caption>{V.chartCaption}</caption>
        <tbody>
          {MB2_PFOS_CHART.map((bar) => (
            <tr key={bar.label}>
              <th>{bar.label}</th>
              <td>{bar.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function DocTitle({ state, stateId }: { readonly state: string; readonly stateId?: string }) {
  return (
    <div className="dt">
      <span>{V.docTitle}</span>
      <span id={stateId}>{state}</span>
    </div>
  );
}

export function DraftDoc() {
  return (
    <>
      <div className="doc">
        <DocTitle state={V.draftState} />
        <p>
          {ORIGINAL_SENTENCE}
          <RichText text={DRAFT_TAIL} />
        </p>
      </div>
      <div className="srcnote">
        <i />
        {V.srcNote}
      </div>
    </>
  );
}

export function CheckedDoc() {
  return (
    <>
      <div className="doc">
        <DocTitle state={V.checkedState} />
        <p>
          <span className="bad">{FLAGGED_CLAIM}</span>
          {ORIGINAL_SENTENCE.slice(FLAGGED_CLAIM.length)}
          <RichText text={DRAFT_TAIL} />
        </p>
      </div>
      <div className="flag">
        <span className="dot">
          <Icon name="cross" />
        </span>
        <div>
          <b>{V.flagTitle}</b>
          <p>{V.flagText}</p>
        </div>
      </div>
      <div className="flag ok" style={{ marginTop: 10 }}>
        <span className="dot">
          <Icon name="tick" />
        </span>
        <div>
          <b>{V.okTitle}</b>
          <p>{V.okText}</p>
        </div>
      </div>
    </>
  );
}

export function RuleRulers() {
  return (
    <>
      <p className="vt">
        <RichText text={V.rulerTitle} />
      </p>
      {V.rulers.map((ruler) => (
        <div className="ruler" key={ruler.name}>
          <div className="rh">
            <b>{ruler.name}</b>
            <span>{ruler.basis}</span>
          </div>
          <div className="track">
            <span
              className={ruler.over === null ? 'fillbar' : 'fillbar over'}
              style={{ width: ruler.fill, ...(ruler.over === null ? {} : ({ '--lim': ruler.over } as CSSProperties)) }}
            />
            <span className="limit" style={{ left: ruler.limitAt }}>
              <em>{ruler.limitLabel}</em>
            </span>
          </div>
          <div className={ruler.over === null ? 'verdict u' : 'verdict o'}>{ruler.verdict}</div>
        </div>
      ))}
      <div className="scale" aria-hidden="true">
        {V.scale.map((tick) => (
          <span key={tick}>{tick}</span>
        ))}
      </div>
    </>
  );
}
