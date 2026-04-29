import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';

import {
  CONVERSATION_READ_EVENT,
  notifyConversationRead,
  useMarkThreadSeen,
} from '@/lib/conversationReadEvents';
import { useConversations } from '@/hooks/useConversations';
import { ConversationListPane } from './ConversationListPane';
import type { ConvSummary } from './ConvRow';

// useConversations + ConversationListPane both reach into useApp() for the
// chatPanel slice (used to gate "active" highlighting + lazy refresh
// triggers). Stub it so the tests focus on the read-event flow without
// pulling in the full AppProvider.
const chatPanelMock = { isOpen: false, conversationId: null, taskId: null, suggestionId: null };
vi.mock('@/context/AppContext', () => ({
  useApp: () => ({
    chatPanel: chatPanelMock,
    openChat: vi.fn(),
  }),
}));

const getConversationsMock = vi.fn();
vi.mock('@/graphql/client', () => ({
  getConversations: (...args: unknown[]) => getConversationsMock(...args),
  fromGraphqlEnum: (value: string | null | undefined) =>
    typeof value === 'string' ? value.toLowerCase() : value,
}));

function makeConv(overrides: Partial<ConvSummary> = {}): ConvSummary {
  return {
    uid: 'conv-1',
    conversationType: 'tenant',
    title: 'Marcus Rivera',
    lastMessageAt: '2026-04-29T01:00:00Z',
    updatedAt: '2026-04-29T01:00:00Z',
    lastMessageBody: 'Tomorrow works',
    lastMessageSenderName: 'Marcus Rivera',
    propertyName: '123 Main St',
    participantCount: 1,
    unreadCount: 2,
    ...overrides,
  };
}

beforeEach(() => {
  getConversationsMock.mockReset();
});

// --- Regression coverage for the badge-snap-back bug ----------------------
//
// Symptom (before fix): clicking a conversation in the dashboard cleared the
// per-conversation badge for a frame, then the badge re-appeared because the
// in-flight conversation-list refresh and the global notification refetch
// both raced against an uncommitted markConversationSeen mutation. The fix
// dispatches CONVERSATION_READ_EVENT *after* the mutation resolves so both
// listeners observe a backend that has already cleared the receipts.
describe('useMarkThreadSeen', () => {
  it('does not dispatch CONVERSATION_READ_EVENT until the mutation resolves', async () => {
    let resolveMutation!: () => void;
    const sendMutation = vi.fn(
      () => new Promise<void>((resolve) => { resolveMutation = () => resolve(); }),
    );
    const eventListener = vi.fn();
    window.addEventListener(CONVERSATION_READ_EVENT, eventListener);

    try {
      const { result } = renderHook(() => useMarkThreadSeen(sendMutation));

      act(() => result.current('conv-1'));

      expect(sendMutation).toHaveBeenCalledOnce();
      expect(sendMutation).toHaveBeenCalledWith('conv-1');
      // Critical: the event must NOT fire while the mutation is still
      // in-flight, otherwise listeners that re-fetch from the API see
      // stale receipt state and the badges snap back.
      expect(eventListener).not.toHaveBeenCalled();

      await act(async () => {
        resolveMutation();
        await Promise.resolve();
      });

      await waitFor(() => expect(eventListener).toHaveBeenCalledTimes(1));
      const detail = (eventListener.mock.calls[0][0] as CustomEvent).detail;
      expect(detail).toEqual({ conversationId: 'conv-1' });
    } finally {
      window.removeEventListener(CONVERSATION_READ_EVENT, eventListener);
    }
  });

  it('dedupes repeat calls so the mutation only fires once per conversation', () => {
    const sendMutation = vi.fn(() => Promise.resolve());
    const { result } = renderHook(() => useMarkThreadSeen(sendMutation));

    act(() => {
      result.current('conv-1');
      result.current('conv-1');
      result.current('conv-2');
    });

    expect(sendMutation).toHaveBeenCalledTimes(2);
    expect(sendMutation.mock.calls.map((call) => call[0])).toEqual(['conv-1', 'conv-2']);
  });

  it('re-allows the mutation when the previous attempt failed', async () => {
    let attempt = 0;
    const sendMutation = vi.fn(() => {
      attempt += 1;
      return attempt === 1 ? Promise.reject(new Error('boom')) : Promise.resolve();
    });
    const { result } = renderHook(() => useMarkThreadSeen(sendMutation));

    await act(async () => {
      result.current('conv-1');
      await Promise.resolve();
    });
    await waitFor(() => expect(sendMutation).toHaveBeenCalledTimes(1));

    await act(async () => {
      result.current('conv-1');
      await Promise.resolve();
    });
    await waitFor(() => expect(sendMutation).toHaveBeenCalledTimes(2));
  });
});

// --- Per-conversation unread badge in the dashboard left rail -------------
describe('ConversationListPane unread badge', () => {
  function Harness() {
    const { conversations, loading } = useConversations('tenant', 20);
    return (
      <ConversationListPane
        title="Chats"
        conversations={conversations}
        loading={loading}
      />
    );
  }

  it('clears the unread badge when CONVERSATION_READ_EVENT fires for that conversation', async () => {
    getConversationsMock.mockResolvedValue({
      conversations: [makeConv({ uid: 'conv-1', unreadCount: 2 })],
    });

    render(<Harness />);

    // Initial render: backend says 2 unread → badge visible.
    expect(await screen.findByLabelText('2 unread messages')).toBeInTheDocument();

    // Simulate the event ChatPanel.markThreadSeen dispatches once the
    // markConversationSeen mutation has committed.
    act(() => notifyConversationRead('conv-1'));

    // Badge gone, conversation row still rendered.
    expect(screen.queryByLabelText('2 unread messages')).not.toBeInTheDocument();
    expect(screen.getByText('Marcus Rivera')).toBeInTheDocument();
  });

  it('leaves badges on other conversations untouched', async () => {
    getConversationsMock.mockResolvedValue({
      conversations: [
        makeConv({ uid: 'conv-1', title: 'Marcus Rivera', unreadCount: 2 }),
        makeConv({ uid: 'conv-2', title: 'Priya Patel', unreadCount: 5 }),
      ],
    });

    render(<Harness />);

    expect(await screen.findByLabelText('2 unread messages')).toBeInTheDocument();
    expect(screen.getByLabelText('5 unread messages')).toBeInTheDocument();

    act(() => notifyConversationRead('conv-1'));

    expect(screen.queryByLabelText('2 unread messages')).not.toBeInTheDocument();
    expect(screen.getByLabelText('5 unread messages')).toBeInTheDocument();
  });
});

// --- End-to-end: markThreadSeen drives the badge dismissal ----------------
//
// Threads the producer (useMarkThreadSeen) and the consumer (useConversations
// listener wired to ConversationListPane) together so the test fails if
// either side regresses on the contract.
describe('Click-to-dismiss flow', () => {
  function Harness({
    onReady,
  }: {
    onReady: (markSeen: (uid: string) => void) => void;
  }) {
    const { conversations, loading } = useConversations('tenant', 20);
    const sendMutation = (uid: string) => Promise.resolve({ uid });
    const markSeen = useMarkThreadSeen(sendMutation);
    onReady(markSeen);
    return (
      <ConversationListPane
        title="Chats"
        conversations={conversations}
        loading={loading}
      />
    );
  }

  it('dismisses the unread badge after markThreadSeen completes its mutation', async () => {
    getConversationsMock.mockResolvedValue({
      conversations: [makeConv({ uid: 'conv-1', unreadCount: 3 })],
    });

    let markSeen!: (uid: string) => void;
    render(<Harness onReady={(fn) => { markSeen = fn; }} />);

    expect(await screen.findByLabelText('3 unread messages')).toBeInTheDocument();

    await act(async () => {
      markSeen('conv-1');
      // Let the resolved mutation chain notifyConversationRead.
      await Promise.resolve();
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(screen.queryByLabelText('3 unread messages')).not.toBeInTheDocument();
    });
  });
});
