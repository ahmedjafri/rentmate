import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { AppLayout } from './AppLayout';

const navigateMock = vi.fn();
const openChatMock = vi.fn();
const closeChatMock = vi.fn();
const markNotificationReadMock = vi.fn(async () => undefined);
const archiveNotificationMock = vi.fn(async () => undefined);

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('./Sidebar', () => ({
  AppSidebar: () => <div data-testid="sidebar" />,
}));

vi.mock('@/components/chat/ChatPanel', () => ({
  ChatPanel: () => <div data-testid="chat-panel" />,
}));

vi.mock('@/components/ui/sidebar', () => ({
  SidebarProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarTrigger: () => <button type="button">Toggle</button>,
}));

vi.mock('@/context/AppContext', () => ({
  useApp: () => ({
    properties: [],
    tenants: [],
    actionDeskTasks: [],
    suggestions: [
      {
        id: 's1',
        title: 'Review lease notice',
        category: 'compliance',
        status: 'pending',
        urgency: 'medium',
        createdAt: new Date('2026-04-24T00:00:00Z'),
        chatThread: [],
      },
    ],
    notifications: [
      {
        id: 'n1',
        kind: 'manager_attention',
        channel: 'in_app',
        deliveryStatus: 'recorded',
        title: 'Task needs your input',
        body: 'Approve the landscaping quote.',
        taskId: '6',
        conversationId: '12',
        createdAt: new Date('2026-04-24T00:00:00Z'),
        readAt: null,
        archivedAt: null,
      },
    ],
    unreadNotificationCount: 1,
    chatPanel: {
      isOpen: false,
      suggestionId: null,
      taskId: null,
      conversationId: null,
      pageContext: null,
      reviewTrigger: null,
    },
    openChat: openChatMock,
    closeChat: closeChatMock,
    markNotificationRead: markNotificationReadMock,
    archiveNotification: archiveNotificationMock,
  }),
}));

describe('AppLayout notifications', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders persisted notifications in the bell popover and opens the linked task', async () => {
    render(
      <MemoryRouter initialEntries={['/properties']}>
        <AppLayout>
          <div>content</div>
        </AppLayout>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByLabelText('2 items needing attention'));

    expect(screen.getByText('Notifications')).toBeInTheDocument();
    expect(screen.getByText('Task needs your input')).toBeInTheDocument();
    expect(screen.getByText('Pending Suggestions')).toBeInTheDocument();
    expect(screen.getByText('Review lease notice')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Task needs your input'));

    await waitFor(() => {
      expect(markNotificationReadMock).toHaveBeenCalledWith('n1');
      expect(navigateMock).toHaveBeenCalledWith('/tasks/6');
    });
  });

  it('archives a notification from the popover', async () => {
    render(
      <MemoryRouter initialEntries={['/properties']}>
        <AppLayout>
          <div>content</div>
        </AppLayout>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByLabelText('2 items needing attention'));
    fireEvent.click(screen.getByText('Archive'));

    await waitFor(() => {
      expect(archiveNotificationMock).toHaveBeenCalledWith('n1');
    });
  });
});
