import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { AppLayout } from './AppLayout';

const navigateMock = vi.fn();
const openChatMock = vi.fn();
const closeChatMock = vi.fn();
const markNotificationReadMock = vi.fn(async () => undefined);
const archiveNotificationMock = vi.fn(async () => undefined);
const defaultNotification = {
  id: 'n1',
  kind: 'manager_attention',
  channel: 'in_app',
  deliveryStatus: 'recorded',
  title: 'Task needs your input',
  body: 'Approve the landscaping quote.',
  taskId: '6',
  conversationId: '12',
  conversationUid: 'conv-vendor',
  messageId: 'vendor-msg-1',
  createdAt: new Date('2026-04-24T00:00:00Z'),
  readAt: null,
  archivedAt: null,
};
let mockNotifications = [defaultNotification];

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
    notifications: mockNotifications,
    unreadNotificationCount: mockNotifications.filter(n => !n.readAt && !n.archivedAt).length,
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
    mockNotifications = [defaultNotification];
  });

  it('renders persisted notifications in the bell popover and opens the linked task', async () => {
    render(
      <MemoryRouter initialEntries={['/properties']}>
        <AppLayout>
          <div>content</div>
        </AppLayout>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByLabelText('1 unread notifications'));

    expect(screen.getByText('Notifications')).toBeInTheDocument();
    expect(screen.getByText('Task needs your input')).toBeInTheDocument();
    expect(screen.getByText('Pending Suggestions')).toBeInTheDocument();
    expect(screen.getByText('Review lease notice')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Task needs your input'));

    await waitFor(() => {
      expect(markNotificationReadMock).toHaveBeenCalledWith('n1');
      expect(navigateMock).toHaveBeenCalledWith('/tasks/6?conversation=conv-vendor&message=vendor-msg-1');
    });
  });

  it('dismisses a notification from the popover with an icon button', async () => {
    render(
      <MemoryRouter initialEntries={['/properties']}>
        <AppLayout>
          <div>content</div>
        </AppLayout>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByLabelText('1 unread notifications'));

    expect(screen.queryByText('Archive')).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Dismiss notification'));

    await waitFor(() => {
      expect(archiveNotificationMock).toHaveBeenCalledWith('n1');
    });
  });

  it('does not show a badge when visible notifications are read', () => {
    mockNotifications = [
      { ...defaultNotification, readAt: new Date('2026-04-24T01:00:00Z') },
      { ...defaultNotification, id: 'n2', readAt: new Date('2026-04-24T02:00:00Z') },
    ];

    render(
      <MemoryRouter initialEntries={['/properties']}>
        <AppLayout>
          <div>content</div>
        </AppLayout>
      </MemoryRouter>,
    );

    expect(screen.getByLabelText('Notifications')).toBeInTheDocument();
    expect(screen.queryByLabelText(/unread notifications/)).not.toBeInTheDocument();
  });
});
