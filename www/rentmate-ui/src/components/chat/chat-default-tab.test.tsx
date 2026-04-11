import { describe, expect, it } from 'vitest';

import { ActionDeskTask } from '@/data/mockData';

import { getDefaultTaskTab } from './ChatPanel';

function makeTask(overrides: Partial<ActionDeskTask> = {}): ActionDeskTask {
  return {
    id: 'task-1',
    title: 'Test task',
    mode: 'manual',
    status: 'active',
    participants: [],
    lastMessage: '',
    lastMessageBy: '',
    lastMessageAt: new Date('2026-04-11T00:00:00Z'),
    unreadCount: 0,
    category: 'maintenance',
    urgency: 'low',
    chatThread: [],
    ...overrides,
  };
}

describe('getDefaultTaskTab', () => {
  it('prefers the external conversation over the AI tab', () => {
    const task = makeTask({
      externalConversationId: 'conv-vendor',
      aiConversationId: 'conv-ai',
      linkedConversations: [
        { uid: 'conv-ai', label: 'AI', conversationType: 'task_ai', messageCount: 0, participants: [] },
        { uid: 'conv-vendor', label: 'Vendor', conversationType: 'vendor', messageCount: 3, participants: [] },
      ],
    });

    expect(getDefaultTaskTab(task)).toBe('conv-vendor');
  });

  it('falls back to the parent conversation when no external conversation is linked', () => {
    const task = makeTask({
      parentConversationId: 'conv-tenant',
      linkedConversations: [
        { uid: 'conv-tenant', label: 'Tenant', conversationType: 'tenant', messageCount: 2, participants: [] },
      ],
    });

    expect(getDefaultTaskTab(task)).toBe('conv-tenant');
  });

  it('falls back to the AI tab when there is no linked tenant/vendor conversation', () => {
    const task = makeTask({
      aiConversationId: 'conv-ai',
      linkedConversations: [
        { uid: 'conv-ai', label: 'AI', conversationType: 'task_ai', messageCount: 1, participants: [] },
      ],
    });

    expect(getDefaultTaskTab(task)).toBe('ai');
  });
});
