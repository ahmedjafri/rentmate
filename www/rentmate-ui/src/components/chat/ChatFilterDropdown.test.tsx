import { describe, it, expect } from 'vitest';

import { chatFilterLabel, type ChatFilter } from './ChatFilterDropdown';

describe('chatFilterLabel', () => {
  it('returns the human-readable label for each filter value', () => {
    const cases: [ChatFilter, string][] = [
      ['all', 'All'],
      ['user_ai', 'RentMate'],
      ['tenant', 'Tenants'],
      ['vendor', 'Vendors'],
    ];
    for (const [value, expected] of cases) {
      expect(chatFilterLabel(value)).toBe(expected);
    }
  });

  it('falls back to "All" for unknown values', () => {
    expect(chatFilterLabel('unknown' as ChatFilter)).toBe('All');
  });
});
