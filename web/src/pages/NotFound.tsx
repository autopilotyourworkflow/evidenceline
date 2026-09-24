import { PageShell } from '../components/PageShell';
import { NOT_FOUND_PAGE as P } from '../content/pages';
import { Link } from '../components/Link';

export function NotFoundPage() {
  return (
    <PageShell title={P.title} lead={<p>{P.lead}</p>} back={false}>
      <div className="linkrow">
        <Link className="btn primary sm" to="/">
          {P.home}
        </Link>
      </div>
    </PageShell>
  );
}
