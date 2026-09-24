import { PageShell, WhenLoaded } from '../components/PageShell';
import { SOURCES_PAGE as P } from '../content/pages';
import { isSoilCriteria, isSources, useJson, type Limit, type Rule, type SoilCriteria, type Sources } from '../lib/data';
import { formatDate, prettyUnits } from '../lib/format';

function appliesTo(limit: Limit): string {
  return limit.applies_to === 'sum' ? `${limit.members.join(' and ')} added together` : `${limit.members.join(', ')} on its own`;
}

function RuleCard({ rule }: { readonly rule: Rule }) {
  return (
    <article className="rulecard" aria-labelledby={`rule-${rule.id}`}>
      <h3 id={`rule-${rule.id}`}>{rule.name}</h3>
      <p className="rule-id">Rule: {rule.id}</p>
      <dl className="facts">
        <dt>Document</dt>
        <dd>{rule.document}</dd>
        <dt>Table</dt>
        <dd>{rule.table}</dd>
        <dt>Page</dt>
        <dd>
          {rule.page} ({rule.page_basis})
        </dd>
        <dt>In WA</dt>
        <dd>{rule.wa_status}</dd>
      </dl>
      <h4>{P.valuesTitle}</h4>
      <ul className="limits">
        {rule.limits.map((limit) => (
          <li key={limit.key}>
            <div className="limit-head">
              <b>{appliesTo(limit)}</b>
              <span className="v">
                {limit.value} {prettyUnits(limit.unit)}
              </span>
            </div>
            <span className="scenario">{limit.scenario}</span>
            <p>{prettyUnits(limit.note)}</p>
          </li>
        ))}
      </ul>
      {rule.links.length > 0 && (
        <>
          <h4>{P.linksTitle}</h4>
          <ul className="doclinks">
            {rule.links.map((link) => (
              <li key={link.url}>
                <a href={link.url} target="_blank" rel="noopener">
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
        </>
      )}
    </article>
  );
}

function SoilTable({ soil }: { readonly soil: SoilCriteria }) {
  return (
    <>
      <p className="note">
        {soil.used_for} {P.soilLandUse}
      </p>
      <div className="tablewrap">
        <table className="dtable stack soiltable">
          <caption className="skip">{P.soilTitle}</caption>
          <thead>
            <tr>
              <th scope="col">Substance</th>
              <th scope="col">{soil.scenario_short} value</th>
              <th scope="col">Document</th>
              <th scope="col">Table and page</th>
            </tr>
          </thead>
          <tbody>
            {soil.criteria.map((c) => (
              <tr key={c.id}>
                <th scope="row" data-label="Substance">
                  <div>
                    {c.label.charAt(0).toUpperCase() + c.label.slice(1)}
                    {c.note !== '' && <small className="prov">{c.note}</small>}
                  </div>
                </th>
                <td data-label={`${soil.scenario_short} value`} className="num">
                  <span className="v">
                    {c.value} {c.unit}
                  </span>
                </td>
                <td data-label="Document">{c.document}</td>
                <td data-label="Table and page">
                  {c.table}, {c.page}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">
        Land use: {soil.scenario}. Values copied from the verified criteria set compiled on {formatDate(soil.verified_on)}.
      </p>
      <div className="linkrow">
        <a className="btn ghost sm" href="/data/soil-criteria.json">
          {P.soilRawLink}
        </a>
      </div>
    </>
  );
}

function SoilSection() {
  const loaded = useJson('soil-criteria.json', isSoilCriteria);
  return (
    <section className="block soil" aria-labelledby="soil">
      <h2 id="soil">{P.soilTitle}</h2>
      <WhenLoaded loaded={loaded}>{(soil) => <SoilTable soil={soil} />}</WhenLoaded>
    </section>
  );
}

function SourcesBody({ sources }: { readonly sources: Sources }) {
  return (
    <>
      <div className="callout">
        <h2>{P.choiceTitle}</h2>
        <p>{sources.choice_note}</p>
        <p>{P.investigationNote}</p>
      </div>
      <section className="block water" aria-labelledby="water">
        <h2 id="water">{P.waterTitle}</h2>
        <p className="note">Values checked against the primary sources on {formatDate(sources.verified_on)}.</p>
        <div className="rules">
          {sources.rules.map((rule) => (
            <RuleCard key={rule.id} rule={rule} />
          ))}
        </div>
        <div className="linkrow">
          <a className="btn ghost sm" href="/data/sources.json">
            {P.rawLink}
          </a>
        </div>
      </section>
      <SoilSection />
      <p className="boundary">{P.notLoaded}</p>
    </>
  );
}

export function SourcesPage() {
  const loaded = useJson('sources.json', isSources);
  return (
    <PageShell title={P.title} lead={<p>{P.lead}</p>}>
      <WhenLoaded loaded={loaded}>{(sources) => <SourcesBody sources={sources} />}</WhenLoaded>
    </PageShell>
  );
}
