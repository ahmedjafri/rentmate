import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { AppProvider, useApp } from '@/context/AppContext';
import { notifyConversationRead } from '@/lib/conversationReadEvents';

// Stub the heavy data hook — AppProvider wraps it for the rest of the app
// state (properties, tenants, etc.) which this test does not exercise.
// Reference-stable empty arrays so the AppProvider effects that watch
// these deps don't fire every render and trigger a re-render loop.
const STABLE_EMPTY = vi.hoisted(() => {
  const noop = () => {};
  return {
    properties: Object.freeze([]) as unknown[],
    tenants: Object.freeze([]) as unknown[],
    vendors: Object.freeze([]) as unknown[],
    actionDeskTasks: Object.freeze([]) as unknown[],
    tickets: Object.freeze([]) as unknown[],
    suggestions: Object.freeze([]) as unknown[],
    isLoading: false,
    error: null as string | null,
    refresh: noop,
  };
});
vi.mock('@/hooks/useApiData', () => ({
  useApiData: () => STABLE_EMPTY,
}));

// Surface the call counter + control the response sequence so the test
// can verify the listener triggers a fresh /api/notifications fetch.
const authFetchMock = vi.fn();
vi.mock('@/lib/auth', () => ({
  authFetch: (...args: unknown[]) => authFetchMock(...args),
}));

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() },
}));

function notificationPayload(unreadCount: number) {
  return Array.from({ length: unreadCount }, (_, i) => ({
    uid: `notif-${i + 1}`,
    kind: 'manager_attention',
    channel: 'in_app',
    delivery_status: 'recorded',
    title: 'Task needs your input: Plumber quote',
    body: 'Approve the $450 quote?',
    task_id: 7,
    conversation_id: 12,
    conversation_uid: 'conv-vendor',
    message_id: `msg-${i + 1}`,
    created_at: '2026-04-29T00:00:00Z',
    read_at: null,
    archived_at: null,
    sent_at: null,
    failed_at: null,
    failure_reason: null,
    extra: null,
  }));
}

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
}

function NotificationBadgeProbe() {
  const { unreadNotificationCount } = useApp();
  return <div data-testid="bell-count">{unreadNotificationCount}</div>;
}

beforeEach(() => {
  authFetchMock.mockReset();
  localStorage.clear();
});

/**
 * Drive /api/notifications via a stateful counter so the AppProvider can
 * make as many calls as it wants (initial mount + location-change effect)
 * without exhausting a `mockResolvedValueOnce` queue and getting
 * `undefined` back, which crashed the test runner with an OOM loop.
 */
function installNotificationsFetch(unreadCounts: number[]) {
  let call = 0;
  authFetchMock.mockImplementation(async (url: string) => {
    if (url === '/api/notifications') {
      const count = unreadCounts[Math.min(call, unreadCounts.length - 1)];
      call += 1;
      return jsonResponse(notificationPayload(count));
    }
    return jsonResponse({});
  });
  return () => call;
}

describe('Bell notification badge dismissal on conversation read', () => {
  it('re-fetches /api/notifications when CONVERSATION_READ_EVENT fires and reflects the cleared count', async () => {
    // The first N fetches return 2 unread; once the event fires and a
    // fresh fetch lands, the next response reports 0 unread (the
    // backend cleared them when the conversation was marked seen).
    let dropToZero = false;
    authFetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/notifications') {
        return jsonResponse(notificationPayload(dropToZero ? 0 : 2));
      }
      return jsonResponse({});
    });

    render(
      <MemoryRouter
        initialEntries={['/']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <AppProvider>
          <NotificationBadgeProbe />
        </AppProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(authFetchMock).toHaveBeenCalledWith('/api/notifications');
      expect(screen.getByTestId('bell-count')).toHaveTextContent('2');
    });

    const fetchesBeforeEvent = authFetchMock.mock.calls.filter(
      (call) => call[0] === '/api/notifications',
    ).length;

    dropToZero = true;
    act(() => {
      notifyConversationRead('conv-vendor');
    });

    // The listener triggers a fresh fetch; the new payload reports 0
    // unread, so the badge clears.
    await waitFor(() => {
      const fetchesAfterEvent = authFetchMock.mock.calls.filter(
        (call) => call[0] === '/api/notifications',
      ).length;
      expect(fetchesAfterEvent).toBeGreaterThan(fetchesBeforeEvent);
      expect(screen.getByTestId('bell-count')).toHaveTextContent('0');
    });
  });

  it('keeps the badge accurate when the backend still reports unread items unrelated to the opened conversation', async () => {
    // Initial: 2 notifications. After opening one conversation the
    // backend marks 1 read (the matching one) but the unrelated one
    // remains unread → bell badge drops from 2 → 1, doesn't disappear.
    let unreadCount = 2;
    authFetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/notifications') {
        return jsonResponse(notificationPayload(unreadCount));
      }
      return jsonResponse({});
    });

    render(
      <MemoryRouter
        initialEntries={['/']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <AppProvider>
          <NotificationBadgeProbe />
        </AppProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('bell-count')).toHaveTextContent('2');
    });

    unreadCount = 1;
    act(() => {
      notifyConversationRead('conv-vendor');
    });

    await waitFor(() => {
      expect(screen.getByTestId('bell-count')).toHaveTextContent('1');
    });
  });
});
