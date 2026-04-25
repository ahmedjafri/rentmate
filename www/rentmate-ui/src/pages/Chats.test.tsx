import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import type { ConvSummary } from '@/components/chat/ConvRow';

const openChatMock = vi.fn();

vi.mock('@/context/AppContext', () => ({
  useApp: () => ({
    chatPanel: {
      isOpen: false,
      suggestionId: null,
      taskId: null,
      conversationId: null,
      pageContext: null,
      reviewTrigger: null,
    },
    openChat: openChatMock,
    closeChat: vi.fn(),
    setChatConversationId: vi.fn(),
  }),
}));

const useConversationsMock = vi.hoisted(() => vi.fn());
vi.mock('@/hooks/useConversations', () => ({
  useConversations: useConversationsMock,
}));

vi.mock('@/components/chat/ChatPanel', () => ({
  ChatPanel: () => <div data-testid="embedded-chat-panel">embedded</div>,
}));

// Stand-in for the Radix DropdownMenu — exposes a plain <button> per
// option so fireEvent.click works without pointer-event simulation.
vi.mock('@/components/chat/ChatFilterDropdown', async () => {
  const actual = await vi.importActual<typeof import('@/components/chat/ChatFilterDropdown')>(
    '@/components/chat/ChatFilterDropdown',
  );
  return {
    ...actual,
    ChatFilterDropdown: ({
      value,
      onChange,
    }: {
      value: string;
      onChange: (next: string) => void;
    }) => (
      <div data-testid="filter-dropdown">
        <span data-testid="filter-value">{value}</span>
        {(['all', 'user_ai', 'tenant', 'vendor'] as const).map((option) => (
          <button
            key={option}
            type="button"
            data-testid={`filter-option-${option}`}
            onClick={() => onChange(option)}
          >
            {option}
          </button>
        ))}
      </div>
    ),
  };
});

vi.mock('@/graphql/client', () => ({
  deleteConversation: vi.fn(),
}));

import Chats from './Chats';

function makeConv(uid: string, conversationType: ConvSummary['conversationType']): ConvSummary {
  return {
    uid,
    conversationType,
    title: `Chat ${uid}`,
    lastMessageAt: null,
    updatedAt: new Date().toISOString(),
    lastMessageBody: null,
    lastMessageSenderName: null,
    propertyName: null,
    participantCount: 0,
    unreadCount: 0,
  };
}

beforeEach(() => {
  openChatMock.mockReset();
  useConversationsMock.mockReset();
  useConversationsMock.mockImplementation((conversationType: string) => ({
    conversations: [makeConv(`${conversationType}-1`, conversationType as ConvSummary['conversationType'])],
    loading: false,
    refresh: vi.fn(),
    removeConversation: vi.fn(),
  }));
});

describe('Chats page', () => {
  it('renders the workspace with no right rail (2-column layout)', () => {
    render(<Chats />);

    expect(screen.getByTestId('embedded-chat-panel')).toBeInTheDocument();
    // The dashboard's right-rail "Action Desk" must NOT appear.
    expect(screen.queryByText(/Action Desk/i)).not.toBeInTheDocument();
  });

  it('queries all three conversation buckets so filter changes are instant', () => {
    render(<Chats />);
    const types = useConversationsMock.mock.calls.map((args) => args[0]);
    expect(types).toContain('user_ai');
    expect(types).toContain('tenant');
    expect(types).toContain('vendor');
  });

  it('mounts the filter dropdown in the header with All as the default value', () => {
    render(<Chats />);
    expect(screen.getByTestId('filter-dropdown')).toBeInTheDocument();
    expect(screen.getByTestId('filter-value')).toHaveTextContent('all');
  });

  it('All view merges every bucket into one list', () => {
    render(<Chats />);

    expect(screen.getByText(/Chat user_ai-1/)).toBeInTheDocument();
    expect(screen.getByText(/Chat tenant-1/)).toBeInTheDocument();
    expect(screen.getByText(/Chat vendor-1/)).toBeInTheDocument();
  });

  it('selecting a non-All filter narrows the list to that bucket', () => {
    render(<Chats />);

    fireEvent.click(screen.getByTestId('filter-option-tenant'));

    expect(screen.getByText(/Chat tenant-1/)).toBeInTheDocument();
    expect(screen.queryByText(/Chat user_ai-1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Chat vendor-1/)).not.toBeInTheDocument();
  });

  it('clicking a row opens it via openChat', () => {
    render(<Chats />);

    const row = screen.getByText(/Chat user_ai-1/).closest('[class*="cursor-pointer"]');
    fireEvent.click(row!);

    expect(openChatMock).toHaveBeenCalledWith({ conversationId: 'user_ai-1' });
  });
});
