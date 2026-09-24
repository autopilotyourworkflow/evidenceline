// The "Technical detail" drawer under each walkthrough step.

import { Fragment } from 'react';
import type { TechBlock } from '../content/types';
import { Icon } from './Icon';
import { RichText } from './RichText';

export function TechDetail({ blocks }: { readonly blocks: readonly TechBlock[] }) {
  return (
    <details>
      <summary>
        <Icon name="chevron" />
        Technical detail
      </summary>
      <div className="tech">
        {blocks.map((block, k) =>
          block.kind === 'dl' ? (
            <dl key={k}>
              {block.rows.map(([term, value]) => (
                <Fragment key={term}>
                  <dt>{term}</dt>
                  <dd>{value}</dd>
                </Fragment>
              ))}
            </dl>
          ) : (
            <p key={k}>
              <RichText text={block.text} />
            </p>
          ),
        )}
      </div>
    </details>
  );
}
