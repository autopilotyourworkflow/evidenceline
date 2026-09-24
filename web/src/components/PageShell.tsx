// Layout for the detail pages: the labelling notice, a quiet page header (no label above the heading), then the content.

import type { ReactNode } from 'react';
import { BACK_TO_OVERVIEW, CONCEPT_NOTICE } from '../content/site';
import type { Loaded } from '../lib/data';
import { Link } from './Link';

type PageShellProps = {
  readonly title: string;
  readonly lead: ReactNode;
  /** A plain link back to the landing page under the lead. Off on pages that already offer one. */
  readonly back?: boolean;
  readonly children: ReactNode;
};

export function PageShell({ title, lead, back = true, children }: PageShellProps) {
  return (
    <>
      <div className="notice">
        <div className="wrap">{CONCEPT_NOTICE}</div>
      </div>
      <div className="pagehead">
        <div className="wrap">
          <h1 id="h1">{title}</h1>
          <div className="lead">{lead}</div>
          {back && (
            <p className="backlink">
              <Link to="/">{BACK_TO_OVERVIEW}</Link>
            </p>
          )}
        </div>
      </div>
      <div className="pagebody">
        <div className="wrap">{children}</div>
      </div>
    </>
  );
}

/** Renders the loaded data, or a plain message while loading or when the file cannot be used. */
export function WhenLoaded<T>({ loaded, children }: { readonly loaded: Loaded<T>; readonly children: (data: T) => ReactNode }) {
  if (loaded.state === 'loading') {
    return (
      <p className="status-line" role="status">
        Loading the data file.
      </p>
    );
  }
  if (loaded.state === 'error') {
    return (
      <p className="status-line error" role="alert">
        {loaded.message}
      </p>
    );
  }
  return <div data-ready="true">{children(loaded.data)}</div>;
}
