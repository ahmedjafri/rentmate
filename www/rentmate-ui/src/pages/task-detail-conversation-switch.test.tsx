import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import type { ActionDeskTask, ChatMessage } from '@/data/mockData';
import { markTaskSeen as markTaskSeenMutation } from '@/graphql/client';
import { updateTask as updateTaskMutation } from '@/graphql/client';
import TaskDetail from './TaskDetail';

const appStore = vi.hoisted(() => {
  let state: any = {};
  const listeners = new Set<() => void>();
  return {
    getState: () => state,
    setState: (next: any) => {
      state = next;
      listeners.forEach(listener => listener());
    },
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
});

vi.mock('@/context/AppContext', async () => {
  const React = await import('react');
  return {
    useApp: () => {
      React.useSyncExternalStore(appStore.subscribe, appStore.getState, appStore.getState);
      return appStore.getState();
    },
  };
});

vi.mock('@/hooks/useOnboarding', () => ({
  useOnboarding: () => ({
    isActive: false,
    loading: false,
    llmConfigured: true,
    state: null,
    dismiss: vi.fn(),
    choose: vi.fn(),
  }),
}));

vi.mock('@/graphql/client', () => ({
  getConversationMessages: vi.fn(async (conversationId: string) => ({
    conversationMessages:
      conversationId === 'conv-tenant'
        ? [
            {
              uid: 'tenant-msg-1',
              body: 'Hi from tenant',
              messageType: 'message',
              senderName: 'Alice Renter',
              senderType: 'tenant',
              isAi: false,
              isSystem: false,
              draftReply: null,
              approvalStatus: null,
              relatedTaskIds: null,
              suggestionId: null,
              actionCard: null,
              sentAt: '2026-04-24T00:00:00Z',
            },
          ]
        : [],
  })),
  sendMessage: vi.fn(async () => undefined),
  markConversationSeen: vi.fn(async () => ({ markConversationSeen: { uid: 'conv-tenant', unreadCount: 0 } })),
  markTaskSeen: vi.fn(async () => ({ markTaskSeen: { uid: 1, unreadCount: 0 } })),
  updateTask: vi.fn(async () => undefined),
  updateTaskGoal: vi.fn(async () => undefined),
  updateTaskStatus: vi.fn(async () => undefined),
  actOnSuggestion: vi.fn(async () => undefined),
  deleteTask: vi.fn(async () => undefined),
  fromGraphqlEnum: (value: string | null | undefined) => value ?? null,
  getTask: vi.fn(async () => ({ task: null })),
  triggerTaskReview: vi.fn(async () => new Response(null, { status: 200 })),
}));

vi.mock('sonner', () => ({
  toast: {
    error: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock('@/components/chat/ChatInput', () => ({
  ChatInput: React.forwardRef(function MockChatInput(
    props: { placeholder?: string; disabled?: boolean },
    _ref,
  ) {
    return (
      <div data-testid="chat-input" data-placeholder={props.placeholder} data-disabled={String(!!props.disabled)}>
        chat input
      </div>
    );
  }),
}));

function makeTask(overrides: Partial<ActionDeskTask> = {}): ActionDeskTask {
  const aiMessage: ChatMessage = {
    id: 'ai-msg-1',
    role: 'assistant',
    content: 'AI thread ready',
    timestamp: new Date('2026-04-24T00:00:00Z'),
    senderName: 'RentMate',
    senderType: 'ai',
    messageType: 'message',
  };
  return {
    id: 'task-1',
    taskNumber: 1,
    title: 'Follow up with tenant',
    mode: 'manual',
    status: 'active',
    participants: [],
    lastMessage: '',
    lastMessageBy: '',
    lastMessageAt: new Date('2026-04-24T00:00:00Z'),
    unreadCount: 0,
    category: 'rent',
    urgency: 'medium',
    chatThread: [aiMessage],
    goal: 'Resolve the tenant payment issue.',
    steps: [],
    aiConversationId: 'conv-ai',
    externalConversationIds: ['conv-tenant'],
    parentConversationId: 'conv-tenant',
    linkedConversations: [
      {
        uid: 'conv-tenant',
        label: 'Tenant',
        conversationType: 'tenant',
        messageCount: 1,
        unreadCount: 0,
        participants: [{ name: 'Alice Renter', participantType: 'tenant' }],
      },
    ],
    ...overrides,
  };
}

function makeAppState(task: ActionDeskTask) {
  const setState = (updater: (prev: any) => any) => appStore.setState(updater(appStore.getState()));

  return {
    properties: [],
    tenants: [],
    vendors: [],
    suggestions: [],
    tickets: [],
    documents: [],
    actionPolicySettings: {},
    entityContext: {},
    isLoading: false,
    actionDeskTasks: [task],
    chatPanel: {
      isOpen: false,
      suggestionId: null,
      taskId: null,
      conversationId: null,
      pageContext: null,
      reviewTrigger: null,
    },
    getEntityContext: vi.fn(() => ''),
    setEntityContext: vi.fn(),
    updateSuggestionStatus: vi.fn(),
    updateSuggestion: vi.fn(),
    addChatMessage: vi.fn(),
    updateTaskMessage: vi.fn(),
    setTaskMessages: vi.fn(),
    updateTask: vi.fn((taskId: string, updates: Partial<ActionDeskTask>) => {
      setState(prev => ({
        ...prev,
        actionDeskTasks: prev.actionDeskTasks.map((task: ActionDeskTask) =>
          task.id === taskId ? { ...task, ...updates } : task,
        ),
      }));
    }),
    addTask: vi.fn(),
    removeTask: vi.fn(),
    addProperty: vi.fn(),
    updateProperty: vi.fn(),
    removeProperty: vi.fn(),
    addTenant: vi.fn(),
    updateTenant: vi.fn(),
    removeTenant: vi.fn(),
    addVendor: vi.fn(),
    updateVendor: vi.fn(),
    removeVendor: vi.fn(),
    addDocument: vi.fn(),
    updateDocument: vi.fn(),
    replaceDocument: vi.fn(),
    removeDocument: vi.fn(),
    refreshData: vi.fn(),
    closeChat: vi.fn(() => {
      setState(prev => ({
        ...prev,
        chatPanel: {
          isOpen: false,
          suggestionId: null,
          taskId: null,
          conversationId: null,
          pageContext: null,
          reviewTrigger: null,
        },
      }));
    }),
    openChat: vi.fn((opts?: { suggestionId?: string | null; taskId?: string | null; pageContext?: string | null; conversationId?: string | null }) => {
      setState(prev => ({
        ...prev,
        chatPanel: {
          isOpen: true,
          taskId: opts?.taskId ?? null,
          suggestionId: opts?.suggestionId ?? null,
          conversationId: opts?.conversationId ?? null,
          pageContext: opts?.pageContext ?? null,
          reviewTrigger: null,
        },
      }));
    }),
    setChatConversationId: vi.fn((conversationId: string) => {
      setState(prev => ({
        ...prev,
        chatPanel: {
          ...prev.chatPanel,
          conversationId,
        },
      }));
    }),
    triggerReviewStream: vi.fn(),
    setActionPolicySettings: vi.fn(),
  };
}

describe('TaskDetail conversation switching', () => {
  beforeEach(() => {
    appStore.setState({});
    vi.clearAllMocks();
    Object.defineProperty(window.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    });
  });

  it('lets the manager switch from tenant chat back to the AI thread', async () => {
    const task = makeTask();
    appStore.setState(makeAppState(task));

    const { container } = render(
      <MemoryRouter initialEntries={['/tasks/task-1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('AI thread ready')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Alice Renter'));
    await waitFor(() => {
      expect(screen.getByText('Hi from tenant')).toBeInTheDocument();
    });

    const leftRail = container.querySelector('div.grid > aside');
    expect(leftRail).toBeTruthy();
    const rentMateBadges = within(leftRail as HTMLElement).getAllByText('RentMate');
    fireEvent.click(rentMateBadges[0]);

    await waitFor(() => {
      expect(screen.getByText('AI thread ready')).toBeInTheDocument();
    });
  });

  it('marks the task seen when the task detail opens', async () => {
    const task = makeTask({ unreadCount: 3 });
    appStore.setState(makeAppState(task));

    render(
      <MemoryRouter initialEntries={['/tasks/task-1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(markTaskSeenMutation).toHaveBeenCalledWith('task-1');
    });

    expect(appStore.getState().actionDeskTasks[0].unreadCount).toBe(0);
  });

  it('opens the linked conversation from the URL and scrolls to the target message', async () => {
    const task = makeTask();
    appStore.setState(makeAppState(task));

    render(
      <MemoryRouter initialEntries={['/tasks/task-1?conversation=conv-tenant&message=tenant-msg-1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('Hi from tenant')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' });
    });
  });

  it('shows unread indicators on linked task conversations', async () => {
    const task = makeTask({
      linkedConversations: [
        {
          uid: 'conv-tenant',
          label: 'Tenant',
          conversationType: 'tenant',
          messageCount: 1,
          unreadCount: 2,
          participants: [{ name: 'Alice Renter', participantType: 'tenant' }],
        },
      ],
    });
    appStore.setState(makeAppState(task));

    render(
      <MemoryRouter initialEntries={['/tasks/task-1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByLabelText('2 unread messages')).toBeInTheDocument();
  });

  it('lets the manager switch back to AI when the AI conversation is present in linkedConversations', async () => {
    const task = makeTask({
      linkedConversations: [
        {
          uid: 'conv-ai',
          label: 'RentMate',
          conversationType: 'task_ai',
          messageCount: 1,
          participants: [],
        },
        {
          uid: 'conv-tenant',
          label: 'Tenant',
          conversationType: 'tenant',
          messageCount: 1,
          participants: [{ name: 'Alice Renter', participantType: 'tenant' }],
        },
      ],
    });
    appStore.setState(makeAppState(task));

    const { container } = render(
      <MemoryRouter initialEntries={['/tasks/task-1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('AI thread ready')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Alice Renter'));
    await waitFor(() => {
      expect(screen.getByText('Hi from tenant')).toBeInTheDocument();
    });

    const leftRail = container.querySelector('div.grid > aside');
    expect(leftRail).toBeTruthy();
    const rentMateBadges = within(leftRail as HTMLElement).getAllByText('RentMate');
    fireEvent.click(rentMateBadges[0]);

    await waitFor(() => {
      expect(screen.getByText('AI thread ready')).toBeInTheDocument();
    });
  });

  it('treats a linked user_ai conversation as the AI thread and switches back correctly', async () => {
    const task = makeTask({
      aiConversationId: null,
      linkedConversations: [
        {
          uid: 'conv-ai',
          label: 'RentMate',
          conversationType: 'user_ai',
          messageCount: 1,
          participants: [],
        },
        {
          uid: 'conv-tenant',
          label: 'Tenant',
          conversationType: 'tenant',
          messageCount: 1,
          participants: [{ name: 'Alice Renter', participantType: 'tenant' }],
        },
      ],
    });
    appStore.setState(makeAppState(task));

    const { container } = render(
      <MemoryRouter initialEntries={['/tasks/task-1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('AI thread ready')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Alice Renter'));
    await waitFor(() => {
      expect(screen.getByText('Hi from tenant')).toBeInTheDocument();
    });

    const leftRail = container.querySelector('div.grid > aside');
    expect(leftRail).toBeTruthy();
    const rentMateBadges = within(leftRail as HTMLElement).getAllByText('RentMate');
    fireEvent.click(rentMateBadges[0]);

    await waitFor(() => {
      expect(screen.getByText('AI thread ready')).toBeInTheDocument();
    });
  });

  it('updates category, urgency, and status from the task detail page', async () => {
    const task = makeTask();
    appStore.setState(makeAppState(task));

    render(
      <MemoryRouter initialEntries={['/tasks/task-1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    const [categoryTrigger, urgencyTrigger, statusTrigger] = await screen.findAllByRole('combobox');

    fireEvent.click(categoryTrigger);
    fireEvent.click(await screen.findByText('Maintenance'));
    await waitFor(() => {
      expect(updateTaskMutation).toHaveBeenCalledWith({
        uid: 'task-1',
        category: 'maintenance',
        urgency: undefined,
        taskStatus: undefined,
      });
    });

    fireEvent.click(urgencyTrigger);
    fireEvent.click(await screen.findByText('high'));
    await waitFor(() => {
      expect(updateTaskMutation).toHaveBeenCalledWith({
        uid: 'task-1',
        category: undefined,
        urgency: 'high',
        taskStatus: undefined,
      });
    });

    fireEvent.click(statusTrigger);
    fireEvent.click(await screen.findByText('paused'));
    await waitFor(() => {
      expect(updateTaskMutation).toHaveBeenCalledWith({
        uid: 'task-1',
        category: undefined,
        urgency: undefined,
        taskStatus: 'paused',
      });
    });
  });
});
