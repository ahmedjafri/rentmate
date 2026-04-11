import { describe, expect, it } from 'vitest';

import {
  fromGraphqlEnum,
  fromGraphqlTaskStatus,
  toGraphqlConversationType,
  toGraphqlMessageType,
  toGraphqlSuggestionStatus,
  toGraphqlTaskMode,
  toGraphqlTaskStatus,
} from './client';

describe('graphql client enum helpers', () => {
  it('normalizes local enums for GraphQL inputs', () => {
    expect(toGraphqlConversationType('user_ai')).toBe('USER_AI');
    expect(toGraphqlMessageType('internal')).toBe('INTERNAL');
    expect(toGraphqlTaskMode('waiting_approval')).toBe('WAITING_APPROVAL');
    expect(toGraphqlTaskStatus('cancelled')).toBe('DISMISSED');
    expect(toGraphqlSuggestionStatus('pending')).toBe('PENDING');
  });

  it('normalizes GraphQL enums for local UI state', () => {
    expect(fromGraphqlEnum('WAITING_APPROVAL')).toBe('waiting_approval');
    expect(fromGraphqlTaskStatus('DISMISSED')).toBe('cancelled');
    expect(fromGraphqlTaskStatus('RESOLVED')).toBe('resolved');
  });
});
