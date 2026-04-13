import { describe, expect, test } from 'vitest';

import { normalizeActionCard } from './ChatPanel';

describe('normalizeActionCard', () => {
  test('maps snake_case action card links from SSE payloads', () => {
    const card = normalizeActionCard({
      kind: 'document',
      title: 'notice.pdf',
      summary: 'Generated notice',
      links: [
        {
          label: 'Open document',
          entity_type: 'document',
          entity_id: 'doc-1',
        },
      ],
    });

    expect(card?.kind).toBe('document');
    expect(card?.links?.[0]).toEqual({
      label: 'Open document',
      entityType: 'document',
      entityId: 'doc-1',
      propertyId: undefined,
    });
  });
});
