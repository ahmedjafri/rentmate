import { useCallback, useRef, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { X, Bot, Sparkles, Users, Lock, MessageSquare, RotateCcw, Loader2, Trash2, Link as LinkIcon, User, Wrench, ClipboardList } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ChatMessageBubble } from './ChatMessage';
import { ChatInput, ChatInputHandle, PendingAttachment } from './ChatInput';
import { useApp } from '@/context/AppContext';
import { ActionDeskTask, ChatMessage, LinkedConversation, ManagedDocument, categoryLabels } from '@/data/mockData';
import { authFetch } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import { SuggestionOptions } from './SuggestionOptions';
import { MessageSuggestionCard } from './MessageSuggestionCard';
import { getMessageSuggestionSendAction, isMessageSuggestion } from './messageSuggestion';
import { ProgressSteps } from './ProgressSteps';
import { OnboardingChips, OnboardingChoice } from './OnboardingChips';
import { OnboardingProgress } from './OnboardingProgress';
import { useOnboarding } from '@/hooks/useOnboarding';
import { apiMessagesToChatThread } from '@/hooks/useApiData';
import {
  actOnSuggestion,
  deleteTask,
  fromGraphqlEnum,
  getConversation,
  getConversationMessages,
  getTask,
  markConversationSeen,
  sendMessage,
  triggerTaskReview,
  updateTaskStatus,
} from '@/graphql/client';
import { notifyConversationRead } from '@/lib/conversationReadEvents';

export type EmbeddedTaskThreadSelection =
  | { kind: 'ai' }
  | { kind: 'conversation'; id: string; messageId?: string | null };

export function getLinkedConversationTabLabel(
  conversation: Pick<LinkedConversation, 'conversationType' | 'label' | 'participants'>,
  allConversations: Array<Pick<LinkedConversation, 'conversationType' | 'label' | 'participants'>> = [],
): string {
  const isVendor = conversation.conversationType === 'vendor';
  const isTenant = conversation.conversationType === 'tenant';
  if (!isVendor && !isTenant) return conversation.label;

  const roleTitle = isVendor ? 'Vendor' : 'Tenant';
  const sameTypeCount = allConversations.filter(c => c.conversationType === conversation.conversationType).length;
  const participantType = isVendor ? 'vendor' : 'tenant';
  const namedParticipants = (conversation.participants ?? []).filter(
    p => p.participantType === participantType && p.name?.trim(),
  );
  const preferredName =
    sameTypeCount === 1 && namedParticipants.length === 1 && conversation.label.trim().toLowerCase() === roleTitle.toLowerCase()
      ? namedParticipants[0].name.trim()
      : conversation.label;

  return `${roleTitle}: ${preferredName}`;
}

export function getDefaultTaskTab(_task: ActionDeskTask | null | undefined): string {
  // Always default to the AI thread when landing on a task — that's the
  // manager's primary agent-facing surface. External conversations are
  // one click away in the left rail.
  return 'ai';
}

function isAiConversationType(conversationType: string | null | undefined): boolean {
  return conversationType === 'task_ai' || conversationType === 'suggestion_ai' || conversationType === 'user_ai';
}

function getTaskAiConversationId(task: ActionDeskTask | null | undefined): string | null {
  if (!task) return null;
  if (task.aiConversationId) return task.aiConversationId;
  const linkedAiConversation = (task.linkedConversations ?? []).find(
    lc => isAiConversationType(lc.conversationType),
  );
  return linkedAiConversation?.uid ?? null;
}

function apiSenderType(message: {
  isAi?: boolean | null;
  senderType?: string | null;
}): ChatMessage['senderType'] {
  if (message.isAi) return 'ai';
  if (message.senderType === 'external_contact') return 'vendor';
  if (message.senderType === 'tenant') return 'tenant';
  return 'manager';
}

function backendStatusToFrontend(status: string): ManagedDocument['status'] {
  if (status === 'done') return 'ready';
  if (status === 'error') return 'error';
  if (status === 'pending' || status === 'processing') return 'analyzing';
  return 'analyzing';
}

export function normalizeActionCard(card: any): ChatMessage['actionCard'] | undefined {
  if (!card) return undefined;
  return {
    kind: card.kind as NonNullable<ChatMessage['actionCard']>['kind'],
    title: card.title,
    summary: card.summary ?? undefined,
    fields: card.fields?.map((field: any) => ({ label: field.label, value: field.value })) ?? undefined,
    links: card.links?.map((link: any) => ({
      label: link.label,
      entityType: (link.entityType ?? link.entity_type) as 'suggestion' | 'property' | 'tenant' | 'unit' | 'document',
      entityId: link.entityId ?? link.entity_id,
      propertyId: link.propertyId ?? link.property_id ?? undefined,
    })) ?? undefined,
    units: card.units?.map((unit: any) => ({
      uid: unit.uid,
      label: unit.label,
      propertyId: unit.propertyId ?? unit.property_id,
    })) ?? undefined,
  };
}

function getLastSentMessage(messages: ChatMessage[]): string | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role !== 'user') continue;
    if ((message.senderType ?? 'manager') !== 'manager') continue;
    const content = message.content?.trim();
    if (content) return content;
  }
  return undefined;
}

function autonomousBlockReason(_task: ActionDeskTask): string | null {
  return null;
}

export async function performTaskDismiss(args: {
  taskId: string;
  aiConversationId?: string | null;
  updateTaskStatusFn: typeof updateTaskStatus;
  sendMessageFn: typeof sendMessage;
  addChatMessage: ReturnType<typeof useApp>['addChatMessage'];
  updateTask: ReturnType<typeof useApp>['updateTask'];
  removeTask: ReturnType<typeof useApp>['removeTask'];
  closeChat: ReturnType<typeof useApp>['closeChat'];
  refreshData: ReturnType<typeof useApp>['refreshData'];
}) {
  const { taskId, aiConversationId, updateTaskStatusFn, sendMessageFn, addChatMessage, updateTask, removeTask, closeChat, refreshData } = args;
  await updateTaskStatusFn(taskId, 'dismissed');
  if (aiConversationId) {
    await sendMessageFn({
      conversationId: aiConversationId,
      body: 'Task dismissed — this item will not be re-created by automations.',
      messageType: 'internal',
      senderName: 'RentMate',
      isAi: true,
    });
  }
  addChatMessage({
    taskId,
  }, {
    id: `dismiss-${Date.now()}`,
    role: 'assistant',
    content: 'Task dismissed — this item will not be re-created by automations.',
    timestamp: new Date(),
    senderName: 'RentMate',
    messageType: 'internal',
  });
  updateTask(taskId, { status: 'cancelled' });
  removeTask(taskId);
  closeChat();
  refreshData();
}

export function ChatPanel({
  embedded = false,
  embeddedTaskSelection,
}: {
  embedded?: boolean;
  embeddedTaskSelection?: EmbeddedTaskThreadSelection | null;
} = {}) {
  const { chatPanel, closeChat, openChat, setChatConversationId, suggestions, actionDeskTasks, addChatMessage, updateTaskMessage, setTaskMessages, updateTask, removeTask, updateSuggestionStatus, addDocument, replaceDocument, removeDocument, refreshData } = useApp();
  const [dismissConfirm, setDismissConfirm] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [activeTaskTab, setActiveTaskTab] = useState<string>('ai');
  const [participantMessages, setParticipantMessages] = useState<ChatMessage[]>([]);
  const [participantLoading, setParticipantLoading] = useState(false);
  const [highlightedMessageId, setHighlightedMessageId] = useState<string | null>(null);
  const participantMessageRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const onboarding = useOnboarding();
  const onboardingStartedRef = useRef(false);
  const markedConversationIdsRef = useRef<Set<string>>(new Set());

  const markThreadSeen = useCallback((conversationId: string) => {
    if (markedConversationIdsRef.current.has(conversationId)) return;
    markedConversationIdsRef.current.add(conversationId);
    notifyConversationRead(conversationId);
    void markConversationSeen(conversationId).catch(() => {
      markedConversationIdsRef.current.delete(conversationId);
    });
  }, []);

  const handleDismiss = async () => {
    if (!chatPanel.taskId) return;
    if (!dismissConfirm) {
      setDismissConfirm(true);
      return;
    }
    const taskId = chatPanel.taskId;
    setDismissing(true);
    try {
      await performTaskDismiss({
        taskId,
        aiConversationId: activeTask?.aiConversationId,
        updateTaskStatusFn: updateTaskStatus,
        sendMessageFn: sendMessage,
        addChatMessage,
        updateTask,
        removeTask,
        closeChat,
        refreshData,
      });
      toast.info('Task dismissed');
    } catch {
      toast.error('Failed to dismiss task');
    } finally {
      setDismissing(false);
      setDismissConfirm(false);
    }
  };
  const handleDelete = async () => {
    if (!chatPanel.taskId) return;
    if (!deleteConfirm) {
      setDeleteConfirm(true);
      return;
    }
    const taskId = chatPanel.taskId;
    setDeleting(true);
    try {
      await deleteTask(taskId);
      removeTask(taskId);
      closeChat();
    } catch {
      toast.error('Failed to delete task');
    } finally {
      setDeleting(false);
      setDeleteConfirm(false);
    }
  };

  // Reset confirm states when task changes
  useEffect(() => {
    setDismissConfirm(false);
    setDeleteConfirm(false);
    setParticipantMessages([]);
  }, [chatPanel.taskId]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const chatInputRef = useRef<ChatInputHandle>(null);
  const [pendingAttachments, setPendingAttachments] = useState<import('./ChatInput').PendingAttachment[]>([]);
  // Track in-flight stream IDs so we can reconnect on panel reopen
  const activeStreamIdRef = useRef<string | null>(null);
  const [isTyping, setIsTyping] = useState(false);
  const [progressLog, setProgressLog] = useState<string[]>([]);

  const activeSuggestion = useMemo(() =>
    chatPanel.suggestionId ? suggestions.find(s => s.id === chatPanel.suggestionId) : null,
    [chatPanel.suggestionId, suggestions]
  );
  const activeMessageSuggestion = useMemo(
    () => (isMessageSuggestion(activeSuggestion) ? activeSuggestion : null),
    [activeSuggestion],
  );
  const activeMessageSuggestionSendAction = useMemo(
    () => getMessageSuggestionSendAction(activeMessageSuggestion),
    [activeMessageSuggestion],
  );

  const activeTask = useMemo(() =>
    chatPanel.taskId ? actionDeskTasks.find(t => t.id === chatPanel.taskId) : null,
    [chatPanel.taskId, actionDeskTasks]
  );
  const effectiveTaskTab =
    embedded && embeddedTaskSelection
      ? embeddedTaskSelection.kind === 'ai'
        ? 'ai'
        : embeddedTaskSelection.id
      : activeTaskTab;
  const targetParticipantMessageId =
    embedded && embeddedTaskSelection?.kind === 'conversation'
      ? embeddedTaskSelection.messageId ?? null
      : null;
  const activeTaskSuggestionTargetTab = activeSuggestion?.targetConversationId ?? 'ai';
  const shouldShowActiveTaskSuggestion = Boolean(
    activeTask && activeSuggestion && effectiveTaskTab === activeTaskSuggestionTargetTab,
  );

  // DB-backed conversation messages (for conversationId-based chats)
  const [convMessages, setConvMessages] = useState<ChatMessage[]>([]);
  const [convTaskLink, setConvTaskLink] = useState<{ taskId: string; taskTitle: string | null } | null>(null);
  const [activeConvType, setActiveConvType] = useState<string | null>(null);
  const activeConversationId = chatPanel.conversationId;

  // Fetch the conversation's task linkage + conversation_type so the
  // middle pane can show a "Task #N — <title>" header when the loaded
  // conversation belongs to a task, and so we can disable the input for
  // read-only mirror conversations (external chat thread snapshots).
  useEffect(() => {
    if (!activeConversationId || activeTask) {
      setConvTaskLink(null);
      setActiveConvType(null);
      return;
    }
    let cancelled = false;
    getConversation(activeConversationId)
      .then((res) => {
        if (cancelled) return;
        const conv = res.conversation;
        if (conv?.taskId) {
          setConvTaskLink({ taskId: conv.taskId, taskTitle: conv.taskTitle ?? null });
        } else {
          setConvTaskLink(null);
        }
        setActiveConvType(conv?.conversationType ?? null);
      })
      .catch(() => {
        if (!cancelled) {
          setConvTaskLink(null);
          setActiveConvType(null);
        }
      });
    return () => { cancelled = true; };
  }, [activeConversationId, activeTask]);

  // Mirror conversations are read-only snapshots of threads from
  // external chat platforms — replies happen on the source platform,
  // not in rentmate. Disable the composer and surface a banner so PMs
  // aren't confused why their messages bounce off the read-only guard
  // in ``chat_service.send_message``.
  const isReadOnlyConv = activeConvType === 'mirrored_chat'
    || activeConvType === 'MIRRORED_CHAT';

  useEffect(() => {
    if (activeTask || (activeSuggestion && !activeConversationId)) { setConvMessages([]); return; }
    if (!activeConversationId) { setConvMessages([]); onboardingStartedRef.current = false; return; }
    getConversationMessages(activeConversationId).then(result => {
      markThreadSeen(activeConversationId);
      setConvMessages((result.conversationMessages ?? []).map(m => ({
        id: m.uid,
        role: m.isAi ? 'assistant' as const : 'user' as const,
        content: m.body,
        timestamp: new Date(m.sentAt),
        senderName: m.senderName,
        senderType: apiSenderType(m),
        messageType: (fromGraphqlEnum(m.messageType) as ChatMessage['messageType']) ?? 'message',
        draftReply: m.draftReply ?? undefined,
        suggestionId: m.suggestionId ?? undefined,
        actionCard: m.actionCard ? {
          kind: m.actionCard.kind as NonNullable<ChatMessage['actionCard']>['kind'],
          title: m.actionCard.title,
          summary: m.actionCard.summary ?? undefined,
          fields: m.actionCard.fields?.map((field) => ({ label: field.label, value: field.value })) ?? undefined,
          links: m.actionCard.links?.map((link) => ({
            label: link.label,
            entityType: link.entityType as 'suggestion' | 'property' | 'tenant' | 'unit' | 'document',
            entityId: link.entityId,
            propertyId: link.propertyId ?? undefined,
          })) ?? undefined,
          units: m.actionCard.units?.map((unit) => ({
            uid: unit.uid,
            label: unit.label,
            propertyId: unit.propertyId,
          })) ?? undefined,
        } : undefined,
      })));
    }).catch(() => {});
  }, [activeConversationId, activeTask, activeSuggestion, markThreadSeen]);

  const messages = activeTask
    ? activeTask.chatThread
    : activeConversationId
      ? convMessages
      : activeSuggestion
        ? activeSuggestion.chatThread
        : convMessages;

  const isAutonomous = activeTask?.mode === 'autonomous';

  // Keep the render pipeline uniform without inferring conversation scope
  // from sender type. Tenant/vendor conversations are external as a whole,
  // including outbound manager/RentMate messages.
  const renderedItems = useMemo(() => {
    type Item =
      | { kind: 'msg'; msg: typeof messages[0] }
      | { kind: 'divider'; label: string; key: string };
    return messages.map((msg): Item => ({ kind: 'msg', msg }));
  }, [messages]);

  // For each question card, the next user message in the same thread
  // counts as the answer — render it inline + flip the card to the
  // "Answered" state. Computed once per messages-update so individual
  // bubbles don't have to scan the list themselves.
  const questionAnswers = useMemo(() => {
    const answers = new Map<string, string>();
    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i];
      if (msg.messageType !== 'action' || msg.actionCard?.kind !== 'question') continue;
      for (let j = i + 1; j < messages.length; j++) {
        const candidate = messages[j];
        const candidateSender = candidate.senderType ?? (candidate.role === 'assistant' ? 'ai' : 'manager');
        if (candidateSender !== 'manager') continue;
        if (candidate.messageType && candidate.messageType !== 'message') continue;
        const body = (candidate.content ?? '').trim();
        if (!body) continue;
        answers.set(msg.id, body);
        break;
      }
    }
    return answers;
  }, [messages]);

  const aiRenderedItems = useMemo(() =>
    renderedItems.filter(item =>
      item.kind === 'divider'
        ? false
        : item.msg.senderType !== 'tenant' && item.msg.senderType !== 'vendor'
    ),
    [renderedItems]
  );

  useEffect(() => {
    if (
      targetParticipantMessageId
      && participantMessages.some(message => message.id === targetParticipantMessageId)
    ) {
      return;
    }
    // ScrollArea renders a viewport child — scroll that instead of the wrapper
    const el = scrollRef.current;
    if (!el) return;
    const viewport = el.querySelector('[data-radix-scroll-area-viewport]') as HTMLElement | null;
    const target = viewport ?? el;
    setTimeout(() => { target.scrollTop = target.scrollHeight; }, 50);
  }, [messages, convMessages, participantMessages, isTyping, progressLog, targetParticipantMessageId]);

  // Refresh task messages from DB whenever a task is opened + poll for new ones
  const loadTaskMessages = (taskId: string) => {
    getTask(taskId).then(result => {
      if (result.task) {
        setTaskMessages(taskId, apiMessagesToChatThread(result.task.messages ?? []));
      }
    }).catch(() => {});
  };

  useEffect(() => {
    if (!chatPanel.taskId) return;
    const taskId = chatPanel.taskId;
    loadTaskMessages(taskId);
    const interval = setInterval(() => loadTaskMessages(taskId), 5000);
    return () => clearInterval(interval);
  }, [chatPanel.taskId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Trigger onboarding opening message when fresh account detected
  useEffect(() => {
    if (
      !onboarding.isActive ||
      onboarding.state?.path_picked ||
      onboarding.loading ||
      chatPanel.taskId ||
      chatPanel.suggestionId ||
      convMessages.length > 0 ||
      isTyping ||
      onboardingStartedRef.current
    ) return;

    onboardingStartedRef.current = true;

    if (!onboarding.llmConfigured) {
      // LLM not configured — inject a canned welcome without calling the AI
      const welcomeMsg: ChatMessage = {
        id: `msg-onboard-welcome-${Date.now()}`,
        role: 'assistant',
        content: "Welcome to RentMate! I'm your AI property management assistant.\n\nBefore we get started, you'll need to connect an AI model so I can help you. You can use any OpenAI-compatible API — OpenAI, Anthropic, DeepSeek, a local Ollama server, or any other provider.",
        timestamp: new Date(),
        senderName: 'RentMate',
        senderType: 'ai',
        messageType: 'message',
      };
      setConvMessages(prev => [...prev, welcomeMsg]);
    } else {
      // LLM is configured — ask the AI to send a welcome only if no AI
      // messages exist yet (avoids duplicate welcome on page reload)
      const hasAiMessage = convMessages.some(m => m.role === 'assistant');
      if (!hasAiMessage) {
        callAI('[onboarding:start]');
      }
    }
  }, [onboarding.isActive, onboarding.loading, onboarding.llmConfigured, onboarding.state?.path_picked, chatPanel.taskId, chatPanel.suggestionId, convMessages.length, isTyping]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load + poll participant messages when chat tab is active
  const loadParticipantMessages = (convoId: string, showLoading = false) => {
    if (showLoading) setParticipantLoading(true);
    getConversationMessages(convoId).then(result => {
      markThreadSeen(convoId);
      const msgs: ChatMessage[] = (result.conversationMessages ?? []).map(m => {
        return {
          id: m.uid,
          role: m.isAi ? 'assistant' as const : 'user' as const,
          content: m.body,
          timestamp: new Date(m.sentAt),
          senderName: m.senderName,
          senderType: apiSenderType(m),
          messageType: (fromGraphqlEnum(m.messageType) as ChatMessage['messageType']) ?? 'message',
          actionCard: m.actionCard ? {
            kind: m.actionCard.kind as NonNullable<ChatMessage['actionCard']>['kind'],
            title: m.actionCard.title,
            summary: m.actionCard.summary ?? undefined,
            fields: m.actionCard.fields?.map((field) => ({ label: field.label, value: field.value })) ?? undefined,
            links: m.actionCard.links?.map((link) => ({
              label: link.label,
              entityType: link.entityType as 'suggestion' | 'property' | 'tenant' | 'unit' | 'document',
              entityId: link.entityId,
              propertyId: link.propertyId ?? undefined,
            })) ?? undefined,
            units: m.actionCard.units?.map((unit) => ({
              uid: unit.uid,
              label: unit.label,
              propertyId: unit.propertyId,
            })) ?? undefined,
          } : undefined,
        };
      });
      setParticipantMessages(msgs);
    }).catch(() => {}).finally(() => { if (showLoading) setParticipantLoading(false); });
  };

  // Determine the non-AI linked conversations
  const linkedChats: LinkedConversation[] = (activeTask?.linkedConversations ?? []).filter(
    lc => !isAiConversationType(lc.conversationType)
  );

  const lastTaskIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (embedded && embeddedTaskSelection) return;
    const currentTaskId = activeTask?.id ?? null;
    const taskChanged = lastTaskIdRef.current !== currentTaskId;
    lastTaskIdRef.current = currentTaskId;

    setActiveTaskTab(prev => {
      if (!activeTask) return 'ai';
      if (taskChanged) return getDefaultTaskTab(activeTask);
      if (prev === 'ai' || prev === 'progress') return prev;
      const stillExists = linkedChats.some(chat => chat.uid === prev);
      return stillExists ? prev : getDefaultTaskTab(activeTask);
    });
  }, [activeTask?.id, linkedChats, activeTask, embedded, embeddedTaskSelection]);

  // Sync the active tab with any external conversation selection (e.g. the
  // left-rail ConvRow in TaskDetail calls setChatConversationId).
  useEffect(() => {
    if (embedded && embeddedTaskSelection) return;
    if (!activeTask) return;
    const convId = chatPanel.conversationId;
    if (!convId) return;
    if (convId === getTaskAiConversationId(activeTask)) {
      setActiveTaskTab('ai');
      return;
    }
    if (linkedChats.some(chat => chat.uid === convId)) {
      setActiveTaskTab(convId);
    }
  }, [chatPanel.conversationId, activeTask, linkedChats, embedded, embeddedTaskSelection]);

  // Load participant messages when a linked conversation tab is active
  useEffect(() => {
    if (effectiveTaskTab === 'ai' || !effectiveTaskTab) return;
    // activeTaskTab is a conversation UID
    const convoId = effectiveTaskTab;
    loadParticipantMessages(convoId, true);
    const interval = setInterval(() => loadParticipantMessages(convoId), 5000);
    return () => clearInterval(interval);
  }, [effectiveTaskTab]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!targetParticipantMessageId || effectiveTaskTab === 'ai') return;
    const node = participantMessageRefs.current[targetParticipantMessageId];
    if (!node) return;
    node.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setHighlightedMessageId(targetParticipantMessageId);
    const timeout = window.setTimeout(() => {
      setHighlightedMessageId(current => (
        current === targetParticipantMessageId ? null : current
      ));
    }, 1800);
    return () => window.clearTimeout(timeout);
  }, [effectiveTaskTab, participantMessages, targetParticipantMessageId]);

  // External trigger — the Trigger Agent button in TaskDetail bumps
  // chatPanel.reviewTrigger.nonce to ask this panel to run an SSE review
  // stream against the active task. Reuses the same isTyping/progressLog
  // rendering the normal chat stream uses, so the agent-thinking UI is
  // identical whether a review was triggered by the user or by the
  // background loop.
  const reviewTrigger = chatPanel.reviewTrigger;
  useEffect(() => {
    if (!reviewTrigger) return;
    if (!activeTask || String(activeTask.id) !== String(reviewTrigger.taskId)) return;

    const controller = new AbortController();
    setIsTyping(true);
    setProgressLog([]);
    // Make sure the AI tab is on screen.
    setActiveTaskTab('ai');

    (async () => {
      try {
        const res = await triggerTaskReview(reviewTrigger.taskId);
        if (!res.ok || !res.body) {
          setIsTyping(false);
          toast.error('Agent review failed to start');
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop() ?? '';
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const event = JSON.parse(line.slice(6));
              if (event.type === 'progress') {
                setProgressLog(prev => [...prev, event.text as string]);
              } else if (event.type === 'done') {
                setIsTyping(false);
                setProgressLog([]);
                refreshData();
                return;
              } else if (event.type === 'error') {
                setIsTyping(false);
                toast.error(`Agent error: ${event.message}`);
                return;
              }
            } catch { /* skip malformed */ }
          }
        }
        setIsTyping(false);
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          setIsTyping(false);
          toast.error(err instanceof Error ? err.message : 'Agent review failed');
        }
      }
    })();

    return () => controller.abort();
  }, [reviewTrigger?.nonce, activeTask?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reconnect to an in-flight chat when the panel opens.
  // If the agent is still running, stream its remaining progress so the user
  // sees the thinking indicator and receives the reply when it finishes.
  useEffect(() => {
    const streamId = activeStreamIdRef.current;
    if (!streamId) return;
    const taskId = chatPanel.taskId;
    const controller = new AbortController();

    (async () => {
      try {
        const res = await authFetch(`/chat/stream/${streamId}`, {
          headers: { 'Content-Type': 'application/json' },
          signal: controller.signal,
        });
        if (!res.ok || !res.body) return;

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        let active = false;

        let reconnectDone = false;
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop() ?? '';
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const event = JSON.parse(line.slice(6));
              if (event.type === 'idle') return; // nothing running
              if (!active) {
                active = true;
                setIsTyping(true);
                setProgressLog([]);
              }
              if (event.type === 'progress') {
                setProgressLog(prev => [...prev, event.text as string]);
	              } else if (event.type === 'done') {
	                reconnectDone = true;
	                activeStreamIdRef.current = null;
	                addAiMessage(event.reply, { taskId });
	                appendEffectMessages(event.effect_messages, { taskId });
	                setIsTyping(false);
	              } else if (event.type === 'error') {
                activeStreamIdRef.current = null;
                toast.error('Agent encountered an error');
                setIsTyping(false);
              }
            } catch { /* ignore malformed lines */ }
          }
        }

        // Stream ended without `done` — pull latest from DB
        if (active && !reconnectDone && taskId) {
          activeStreamIdRef.current = null;
          setTimeout(() => {
            getTask(taskId).then(result => {
              if (result.task) setTaskMessages(taskId, apiMessagesToChatThread(result.task.messages ?? []));
            }).catch(() => {});
          }, 2000);
        }
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          console.warn('[reconnect] stream error:', err);
        }
      } finally {
        setIsTyping(false);
      }
    })();

    return () => controller.abort();
  }, [chatPanel.taskId]); // eslint-disable-line react-hooks/exhaustive-deps

  const addAiMessage = (content: string, context: { taskId?: string | null; suggestionId?: string | null; messageType?: ChatMessage['messageType'] }) => {
    const msg: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: 'assistant',
      content,
      timestamp: new Date(),
      senderName: 'RentMate',
      senderType: 'ai',
      messageType: context.messageType ?? 'message',
    };
    if (!context.taskId && !context.suggestionId) {
      setConvMessages(prev => [...prev, msg]);
    } else {
      addChatMessage(context, msg);
    }
  };

  const appendEffectMessages = (
    effectMessages: Array<{
      id: string;
      body?: string | null;
      message_type?: string | null;
      suggestion_id?: string | null;
      action_card?: ChatMessage['actionCard'];
    }> | undefined,
    context: { taskId?: string | null; suggestionId?: string | null },
  ) => {
    if (!effectMessages) return;
    const documentIds = new Set<string>();
    for (const msg of effectMessages) {
      const normalizedCard = normalizeActionCard(msg.action_card);
      for (const link of normalizedCard?.links ?? []) {
        if (link.entityType === 'document' && link.entityId) documentIds.add(link.entityId);
      }
      const effectMessage: ChatMessage = {
        id: msg.id,
        role: 'assistant',
        content: msg.body ?? '',
        timestamp: new Date(),
        senderName: 'RentMate',
        senderType: 'ai',
        messageType: (msg.message_type as ChatMessage['messageType']) ?? 'message',
        suggestionId: msg.suggestion_id ?? undefined,
        actionCard: normalizedCard,
      };
      if (!context.taskId && !context.suggestionId) {
        setConvMessages(prev => [...prev, effectMessage]);
      } else {
        addChatMessage(context, effectMessage);
      }
    }
    if (documentIds.size > 0) {
      void Promise.all(
        Array.from(documentIds).map(async (documentId) => {
          const res = await authFetch(`/api/document/${documentId}`);
          if (!res.ok) return;
          const data = await res.json();
          addDocument({
            id: data.id,
            fileName: data.filename,
            fileType: data.content_type || 'application/pdf',
            fileSize: 0,
            documentType: data.document_type || 'other',
            status: backendStatusToFrontend(data.status),
            uploadedAt: new Date(data.created_at),
            analyzedAt: data.processed_at ? new Date(data.processed_at) : undefined,
            tags: [],
            aiSummary: data.context || undefined,
            errorMessage: data.error_message || undefined,
            generatedByRentMate: !!data.generated_by_rentmate,
            generationSource: data.extraction_meta?.source ?? undefined,
            ...(context.taskId ? { actionDeskTaskId: context.taskId } : {}),
          });
        }),
      );
    }
  };

  const callAI = async (userMessage: string) => {
    const taskId = chatPanel.taskId;
    const suggestionId = chatPanel.suggestionId;

    setIsTyping(true);
    setProgressLog([]);
    try {
      // Build the request — unified endpoint handles both task and session chats
      const suggestionHint = !taskId && activeSuggestion
        ? `Discussing suggestion: "${activeSuggestion.title}". ${activeSuggestion.body ?? ''}`
        : '';
      const contextPrefix = !taskId ? (suggestionHint || chatPanel.pageContext || '') : '';
      const messageText = contextPrefix ? `[${contextPrefix}]\n\n${userMessage}` : userMessage;

      const payload: Record<string, unknown> = { message: messageText };
      if (taskId) payload.task_id = taskId;
      else if (activeConversationId) payload.conversation_id = activeConversationId;

      const res = await authFetch('/chat/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        let detail = `Service unavailable (${res.status})`;
        try { const d = await res.json(); if (d.detail) detail = String(d.detail); } catch { /* ignore */ }
        throw new Error(detail);
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let receivedDone = false;
      const progressLines: string[] = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        let sseError: Error | null = null;
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let event: {
            type: string;
            text?: string;
            reply?: string;
            stream_id?: string;
            message?: string;
            conversation_id?: string;
            effect_messages?: Array<{ id: string; body?: string | null; message_type?: string | null; suggestion_id?: string | null; action_card?: ChatMessage['actionCard'] }>;
            onboarding?: Parameters<typeof onboarding.update>[0];
          };
          try { event = JSON.parse(line.slice(6)); } catch { continue; }

          if (event.type === 'stream_id') {
            activeStreamIdRef.current = event.stream_id!;
          } else if (event.type === 'progress') {
            progressLines.push(event.text as string);
            setProgressLog(prev => [...prev, event.text as string]);
          } else if (event.type === 'done') {
            receivedDone = true;
            activeStreamIdRef.current = null;
            // Capture the conversation ID from the backend when we didn't have one
            if (event.conversation_id && !activeConversationId && !taskId) {
              setChatConversationId(event.conversation_id);
            }
            // Only persist the thinking trace if there were actual tool-call
            // steps beyond the initial "Thinking…" placeholder.
            const traceLines = progressLines.filter(l => l !== 'Thinking\u2026');
            if (traceLines.length > 0) {
              const thinkingMsg: ChatMessage = {
                id: `thinking-${Date.now()}`,
                role: 'assistant',
                content: traceLines.join('\n'),
                timestamp: new Date(),
                senderName: 'RentMate',
                senderType: 'ai',
                messageType: 'internal',
              };
              if (taskId) {
                addChatMessage({ taskId }, thinkingMsg);
              } else if (!suggestionId) {
                setConvMessages(prev => [...prev, thinkingMsg]);
              } else {
                addChatMessage({ suggestionId }, thinkingMsg);
              }
            }
            if (event.reply) {
              addAiMessage(event.reply, { taskId, suggestionId });
            }
            appendEffectMessages(event.effect_messages, { taskId, suggestionId });
            // Refresh data so new suggestions created by agent tools appear
            refreshData();
            // Update onboarding progress from SSE payload
            if (event.onboarding) {
              onboarding.update(event.onboarding);
            }
          } else if (event.type === 'error') {
            activeStreamIdRef.current = null;
            sseError = new Error(event.message ?? 'AI unavailable');
          }
        }
        if (sseError) throw sseError;
      }

      // Stream closed without done — reload from DB if task chat
      if (!receivedDone && taskId) {
        activeStreamIdRef.current = null;
        setTimeout(() => {
          getTask(taskId).then(result => {
            if (result.task) setTaskMessages(taskId, apiMessagesToChatThread(result.task.messages ?? []));
          }).catch(() => {});
        }, 2000);
      }
    } catch (e) {
      activeStreamIdRef.current = null;
      console.error('Chat error:', e);
      const errorMsg = e instanceof Error ? e.message : "I'm having trouble connecting right now.";
      addAiMessage(errorMsg, { taskId, suggestionId, messageType: 'error' });
      toast.error('RentMate is unavailable right now.');
    } finally {
      setIsTyping(false);
      setProgressLog([]);
    }
  };

  const uploadFile = async (file: File): Promise<{ id: string; filename: string } | null> => {
    const taskId = chatPanel.taskId;
    const docType = onboarding.isActive ? 'lease' : 'other';

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('document_type', docType);
      if (taskId) formData.append('task_id', taskId);
      formData.append('skip_extraction', 'true');
      const res = await authFetch('/api/upload-document', { method: 'POST', body: formData });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const document_id = data.document_id;
      // Only add to document list for new uploads (not duplicates)
      if (!data.duplicate) {
        addDocument({
          id: document_id,
          fileName: file.name,
          fileType: file.type,
          fileSize: file.size,
          documentType: docType,
          status: 'pending',
          uploadedAt: new Date(),
          tags: [],
          ...(taskId ? { actionDeskTaskId: taskId } : {}),
        });
      }
      return { id: document_id, filename: file.name };
    } catch {
      toast.error('Failed to upload file. Please try again.');
      return null;
    }
  };

  const handleSend = (content: string, attachments?: PendingAttachment[], insertedFromMessageId?: string) => {
    const readyAttachments = (attachments ?? []).filter(a => a.documentId);

    // The visible message text — no document IDs
    let displayText = content;
    if (!displayText && readyAttachments.length > 0) {
      const names = readyAttachments.map(a => a.filename).join(', ');
      displayText = `Uploaded ${names}`;
    }

    // The text sent to the agent — includes document IDs so it can use read_document
    let agentText = displayText;
    if (readyAttachments.length > 0) {
      const refs = readyAttachments.map(a => `${a.documentId} (${a.filename})`).join(', ');
      agentText += `\n\n[Attached documents: ${refs}]`;
    }

    const msgAttachments = readyAttachments.map(a => ({
      documentId: a.documentId!,
      filename: a.filename,
    }));

    const userMsg: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: 'user',
      content: displayText,
      timestamp: new Date(),
      senderName: 'You',
      senderType: 'manager',
      messageType: 'message',
      ...(msgAttachments.length > 0 ? { attachments: msgAttachments } : {}),
    };
    if (!chatPanel.taskId && !chatPanel.suggestionId) {
      setConvMessages(prev => [...prev, userMsg]);
    } else {
      addChatMessage(
        { taskId: chatPanel.taskId, suggestionId: chatPanel.suggestionId },
        userMsg
      );
    }

    // Approval workflow: mark approved + switch to autonomous
    if (insertedFromMessageId && chatPanel.taskId) {
      updateTaskMessage(chatPanel.taskId, insertedFromMessageId, { approvalStatus: 'approved' });
      updateTask(chatPanel.taskId, { mode: 'autonomous' });
      setTimeout(() => {
        addChatMessage(
          { taskId: chatPanel.taskId },
          { id: `msg-${Date.now() + 1}`, role: 'assistant', content: 'Approved — switching back to autonomous mode.', timestamp: new Date(), senderName: 'RentMate', messageType: 'internal' }
        );
      }, 300);
      return; // don't call AI for approval confirmations
    }

    callAI(agentText);
  };

  const handleInsertCleared = (messageId: string) => {
    if (chatPanel.taskId) {
      updateTaskMessage(chatPanel.taskId, messageId, { approvalStatus: 'rejected' });
      updateTask(chatPanel.taskId, { mode: 'manual' });
      setTimeout(() => {
        addChatMessage(
          { taskId: chatPanel.taskId },
          { id: `msg-${Date.now()}`, role: 'assistant', content: 'Draft rejected — switching to manual mode. You\'re in control now.', timestamp: new Date(), senderName: 'RentMate', messageType: 'internal' }
        );
      }, 300);
    }
  };

  const headerTitle = activeTask
    ? activeTask.title
    : activeSuggestion
      ? 'Discuss Suggestion'
      : (() => {
          // Derive title from the first user message in the conversation
          const firstUserMsg = messages.find(m => m.role === 'user');
          if (firstUserMsg) {
            const text = firstUserMsg.content.trim();
            return text.length > 50 ? text.slice(0, 50) + '…' : text;
          }
          return 'New Chat';
        })();

  const placeholder = activeTask
    ? 'Reply in this thread...'
    : activeSuggestion
      ? 'Discuss this suggestion...'
      : 'Ask RentMate anything...';
  const lastSentMessage = getLastSentMessage(messages);

  return (
    <div className={cn(
      "flex min-w-0 max-w-full flex-col overflow-hidden bg-card",
      embedded
        ? "h-full w-full"
        : "fixed inset-0 z-50 md:static md:inset-auto md:z-auto md:w-[320px] lg:w-[380px] md:border-l md:shrink-0 md:h-full"
    )}>
      {/* Header — hidden in embedded mode (e.g. TaskDetail provides its own) */}
      {!embedded && (
      <div className="flex items-center justify-between p-4 border-b bg-card shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary shrink-0">
            {activeTask ? <Users className="h-4 w-4 text-primary-foreground" /> : <Bot className="h-4 w-4 text-primary-foreground" />}
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold truncate">{headerTitle}</h3>
            {!activeTask && !activeSuggestion && chatPanel.pageContext && (
              <p className="text-[11px] text-muted-foreground truncate max-w-[180px]">
                {chatPanel.pageContext.split('\n')[0].replace(/^[^:]+:\s*/, '')}
              </p>
            )}
            {activeSuggestion && (
              <p className="text-[11px] text-muted-foreground">
                {categoryLabels[activeSuggestion.category]}
              </p>
            )}
            {activeTask && (
              <div className="flex items-center gap-1.5">
                <p className="text-[11px] text-muted-foreground">
                  {categoryLabels[activeTask.category]}
                </p>
                {activeTask.confidential && (
                  <span className="flex items-center gap-0.5 text-[10px] text-destructive font-medium">
                    <Lock className="h-2.5 w-2.5" />
                    Private
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          {activeTask && (
            <>
              {activeTask.status === 'active' && (
                <Button
                  size="sm"
                  variant="ghost"
                  className={cn(
                    'h-7 rounded-lg text-[11px] px-2',
                    dismissConfirm
                      ? 'text-destructive hover:text-destructive hover:bg-destructive/10'
                      : 'text-destructive/60 hover:text-destructive hover:bg-destructive/10'
                  )}
                  disabled={dismissing}
                  onClick={handleDismiss}
                >
                  {dismissing ? <Loader2 className="h-3 w-3 animate-spin" /> : dismissConfirm ? 'Confirm?' : 'Dismiss'}
                </Button>
              )}
              {activeTask.status === 'cancelled' && (
                <Button
                  size="sm"
                  variant="ghost"
                  className={cn(
                    'h-7 rounded-lg text-[11px] px-2 gap-1',
                    deleteConfirm
                      ? 'text-destructive hover:text-destructive hover:bg-destructive/10'
                      : 'text-muted-foreground/60 hover:text-destructive'
                  )}
                  disabled={deleting}
                  onClick={handleDelete}
                >
                  {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                  {deleteConfirm ? 'Confirm' : null}
                </Button>
              )}
            </>
          )}
          {!embedded && (
            <Button variant="ghost" size="icon" onClick={closeChat} className="h-7 w-7 rounded-lg shrink-0 hover:bg-muted hover:text-muted-foreground">
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>
      )}

      {/* Task Context */}
      {activeTask && (
        <div className="p-3 border-b bg-muted/30 space-y-2">
          {shouldShowActiveTaskSuggestion && activeSuggestion && (
            <div className="rounded-lg border bg-background px-3 py-2">
              <div className="flex items-center gap-1.5 mb-1">
                <Sparkles className="h-3 w-3 text-primary" />
                <span className="text-[11px] font-medium text-primary">
                  {activeSuggestion.targetConversationType === 'vendor'
                    ? 'Suggestion for this vendor thread'
                    : activeSuggestion.targetConversationType === 'tenant'
                      ? 'Suggestion for this tenant thread'
                      : 'Suggestion in this task'}
                </span>
              </div>
              <p className="text-xs font-medium">{activeSuggestion.title}</p>
              <p className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">{activeSuggestion.body}</p>
            </div>
          )}
          {activeTask.confidential && (
            <div className="flex items-center gap-2 rounded-lg bg-destructive/10 border border-destructive/20 px-3 py-2">
              <Lock className="h-3.5 w-3.5 text-destructive shrink-0" />
              <p className="text-[11px] text-destructive font-medium">
                This is a confidential thread. The complainant's identity is not shared with other parties.
              </p>
            </div>
          )}
          {activeTask.mode === 'manual' && (() => {
            const blockReason = autonomousBlockReason(activeTask);
            return blockReason ? (
              <div className="text-[10px] text-muted-foreground italic">{blockReason}</div>
            ) : null;
          })()}
        </div>
      )}

      {/* Suggestion Context */}
      {activeSuggestion && !activeTask && (
        <div className="p-3 border-b bg-muted/30">
          <div className="flex items-center gap-1.5 mb-1">
            <Sparkles className="h-3 w-3 text-primary" />
            <span className="text-[11px] font-medium text-primary">Discussing</span>
          </div>
          <p className="text-xs font-medium">{activeSuggestion.title}</p>
          <p className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">{activeSuggestion.body}</p>
        </div>
      )}

      {activeTask ? (
        <Tabs
          value={effectiveTaskTab}
          onValueChange={v => setActiveTaskTab(v)}
          className="flex-1 flex flex-col min-h-0"
        >
          {/* Tab strip — hidden in embedded mode (TaskDetail's left rail replaces it) */}
          {!embedded && (
          <TabsList className="shrink-0 mx-3 mt-2 mb-0 h-8 self-start gap-1 bg-muted/50">
            <TabsTrigger value="ai" className="text-xs h-6 px-3">AI</TabsTrigger>
            {linkedChats.map(lc => {
              const isVendor = lc.conversationType === 'vendor';
              const isTenant = lc.conversationType === 'tenant';
              const Icon = isVendor ? Wrench : isTenant ? User : null;
              const roleTitle = isVendor ? 'Vendor' : isTenant ? 'Tenant' : lc.conversationType;
              return (
                <TabsTrigger key={lc.uid} value={lc.uid} className="text-xs h-6 px-3 gap-1">
                  {Icon && <Icon className="h-3 w-3" aria-label={roleTitle} />}
                  <span>{getLinkedConversationTabLabel(lc, linkedChats)}</span>
                </TabsTrigger>
              );
            })}
            <TabsTrigger value="progress" className="text-xs h-6 px-3">Progress</TabsTrigger>
          </TabsList>
          )}

          {/* AI tab — internal RentMate thread */}
          <TabsContent value="ai" className="hidden data-[state=active]:flex flex-1 flex-col min-h-0 mt-0">
            {/* AI participant chip */}
            <div className="flex items-center gap-1.5 px-3 py-1.5 border-b bg-muted/20 shrink-0">
              <Badge variant="secondary" className="text-[10px] rounded-lg gap-1 bg-primary/10 text-primary">
                <Bot className="h-3 w-3" />
                RentMate AI
              </Badge>
            </div>
            <ScrollArea className="flex-1 min-w-0 overflow-x-hidden" ref={scrollRef}>
              <div className="w-full min-w-0 max-w-full space-y-4 overflow-x-hidden p-4">
                {aiRenderedItems.length === 0 && !isTyping && (
                  <div className="text-center py-8 text-muted-foreground">
                    <Bot className="h-8 w-8 mx-auto mb-2 opacity-40" />
                    <p className="text-sm font-medium">Ask RentMate about this task</p>
                    <p className="text-xs mt-1">Try: "Draft a message to the tenant"</p>
                  </div>
                )}
                {aiRenderedItems.map(item =>
                  item.kind === 'divider' ? null : (
                    <ChatMessageBubble
                      key={item.msg.id}
                      message={item.msg}
                      taskId={chatPanel.taskId}
                      conversationId={activeTask?.aiConversationId ?? chatPanel.conversationId}
                      questionAnsweredByContent={questionAnswers.get(item.msg.id) ?? null}
                      onSuggestionClick={(sid) => openChat({ suggestionId: sid })}
                    />
                  )
                )}
                {isTyping && (
                  <div data-testid="thinking-row" className="flex items-start gap-2 overflow-hidden text-muted-foreground">
                    <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary/10 shrink-0 mt-0.5">
                      <Bot className="h-3.5 w-3.5 text-primary" />
                    </div>
                    <div data-testid="thinking-bubble" className="flex-1 min-w-0 overflow-hidden py-2 px-3 rounded-2xl bg-muted">
                      {progressLog.length === 0 ? (
                        <div className="flex gap-1 py-0.5">
                          <span className="w-1.5 h-1.5 bg-muted-foreground/50 rounded-full animate-bounce [animation-delay:0ms]" />
                          <span className="w-1.5 h-1.5 bg-muted-foreground/50 rounded-full animate-bounce [animation-delay:150ms]" />
                          <span className="w-1.5 h-1.5 bg-muted-foreground/50 rounded-full animate-bounce [animation-delay:300ms]" />
                        </div>
                      ) : (
                        <div className="space-y-0.5 overflow-hidden">
                          {progressLog.slice(-3).map((line, i, arr) => (
                            <p
                              key={i}
                              data-testid="progress-line"
                              className={`text-[11px] font-mono truncate ${
                                i === arr.length - 1
                                  ? 'text-foreground/80'
                                  : 'text-muted-foreground/50'
                              }`}
                            >
                              {line}
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </ScrollArea>
            {/* AI tab input */}
            {activeTask.status === 'cancelled' || activeTask.status === 'resolved' ? (
              <div className="flex items-center gap-2 px-4 py-3 border-t bg-muted/30 shrink-0">
                <p className="flex-1 text-xs text-muted-foreground">
                  This task is {activeTask.status === 'resolved' ? 'resolved' : 'closed'}.
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 rounded-lg text-[11px] px-2.5 gap-1.5 shrink-0"
                  onClick={async () => {
                    const taskId = chatPanel.taskId!;
                    await updateTaskStatus(taskId, 'active');
                    if (activeTask?.aiConversationId) await sendMessage({
                      conversationId: activeTask.aiConversationId,
                      body: 'Task re-opened.',
                      messageType: 'internal',
                      senderName: 'RentMate',
                      isAi: true,
                    });
                    updateTask(taskId, { status: 'active' });
                    addChatMessage({ taskId }, { id: `msg-${Date.now()}`, role: 'assistant', content: 'Task re-opened.', timestamp: new Date(), senderName: 'RentMate', messageType: 'internal' });
                  }}
                >
                  <RotateCcw className="h-3 w-3" />
                  Re-open
                </Button>
              </div>
            ) : (
              <div className="border-t shrink-0">
                {shouldShowActiveTaskSuggestion && activeSuggestion && activeSuggestion.status === 'pending' && (
                  <div className="border-b bg-muted/20 p-3">
                    {activeMessageSuggestion && activeMessageSuggestionSendAction ? (
                      <MessageSuggestionCard
                        suggestion={activeMessageSuggestion}
                        sendActionLabel={
                          activeMessageSuggestion.options?.find((item) => item.action === activeMessageSuggestionSendAction)?.label ??
                          'Send'
                        }
                        disabled={isTyping}
                        onAccept={async (action) => {
                          const result = await actOnSuggestion(activeMessageSuggestion.id, action);
                          const { status } = result.actOnSuggestion;
                          updateSuggestionStatus(activeMessageSuggestion.id, status.toLowerCase() as 'accepted' | 'dismissed');
                          refreshData();
                        }}
                        onSendEdited={async (body) => {
                          const result = await actOnSuggestion(activeMessageSuggestion.id, 'edit_message', body);
                          const { status } = result.actOnSuggestion;
                          updateSuggestionStatus(activeMessageSuggestion.id, status.toLowerCase() as 'accepted' | 'dismissed');
                          refreshData();
                        }}
                        onDismiss={async () => {
                          const result = await actOnSuggestion(activeMessageSuggestion.id, 'reject_task');
                          const { status } = result.actOnSuggestion;
                          updateSuggestionStatus(activeMessageSuggestion.id, status.toLowerCase() as 'accepted' | 'dismissed');
                          refreshData();
                        }}
                      />
                    ) : (
                      <SuggestionOptions
                        options={activeSuggestion.options}
                        onAction={async (action) => {
                          if (action === 'request_file_upload') {
                            chatInputRef.current?.triggerFileUpload();
                            toast.info('Upload the requested file in this task chat.');
                            return;
                          }
                          const result = await actOnSuggestion(activeSuggestion.id, action);
                          const { status } = result.actOnSuggestion;
                          updateSuggestionStatus(activeSuggestion.id, status.toLowerCase() as 'accepted' | 'dismissed');
                          refreshData();
                        }}
                      />
                    )}
                  </div>
                )}
                {isReadOnlyConv ? (
                <div className="px-4 py-3 text-xs text-muted-foreground bg-muted/40 border-t flex items-center gap-2">
                  <span className="font-medium">Read-only.</span>
                  <span>This thread is mirrored from another platform — send your reply there.</span>
                </div>
              ) : (
                <ChatInput ref={chatInputRef} onSend={handleSend} onInsertCleared={handleInsertCleared} placeholder={placeholder} lastSentMessage={lastSentMessage} disabled={isTyping} uploadFile={uploadFile} attachments={pendingAttachments} setAttachments={setPendingAttachments} />
              )}
              </div>
            )}
          </TabsContent>

          {/* Progress tab */}
          <TabsContent value="progress" className="hidden data-[state=active]:flex flex-1 flex-col min-h-0 mt-0">
            <ScrollArea className="flex-1 overflow-x-hidden">
              <ProgressSteps steps={activeTask.steps} />
            </ScrollArea>
          </TabsContent>

          {/* Linked conversation tabs — one per non-AI conversation */}
          {linkedChats.map(lc => (
            <TabsContent key={lc.uid} value={lc.uid} className="hidden data-[state=active]:flex flex-1 flex-col min-h-0 mt-0">
              {/* Participant chips + portal links */}
              <div className="flex items-center gap-1.5 px-3 py-1.5 border-b bg-muted/20 shrink-0 flex-wrap">
                {(lc.participants ?? []).length === 0 ? (
                  <span className="text-[11px] text-muted-foreground italic">No external participants yet</span>
                ) : (
                  (lc.participants ?? []).map((p, idx) => (
                      <Badge key={p.entityId ?? `${p.name}-${idx}`} variant="secondary" className="text-[10px] rounded-lg gap-1">
                        <span className="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-muted-foreground/20 text-[9px] font-bold">
                          {p.name.charAt(0).toUpperCase()}
                        </span>
                        {p.name}
                        {p.portalUrl && (
                          <button
                            className="ml-0.5 text-primary/60 hover:text-primary transition-colors"
                            title="Copy portal link"
                            onClick={(e) => {
                              e.stopPropagation();
                              try {
                                if (navigator.clipboard?.writeText) {
                                  navigator.clipboard.writeText(p.portalUrl!);
                                } else {
                                  const ta = document.createElement('textarea');
                                  ta.value = p.portalUrl!;
                                  ta.style.cssText = 'position:fixed;left:-9999px';
                                  document.body.appendChild(ta);
                                  ta.select();
                                  document.execCommand('copy');
                                  document.body.removeChild(ta);
                                }
                                toast.success(`${p.participantType === 'vendor' ? 'Vendor' : 'Tenant'} portal link copied`);
                              } catch {
                                toast.error('Failed to copy link');
                              }
                            }}
                          >
                            <LinkIcon className="h-3 w-3" />
                          </button>
                          )}
                        </Badge>
                    ))
                )}
              </div>
              <ScrollArea className="flex-1 overflow-x-hidden">
                <div className="p-4 space-y-4 w-full overflow-x-hidden">
                  {participantLoading && (
                    <div className="flex justify-center py-8">
                      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                    </div>
                  )}
                  {!participantLoading && participantMessages.length === 0 && (
                    <div className="text-center py-8 text-muted-foreground">
                      <MessageSquare className="h-8 w-8 mx-auto mb-2 opacity-40" />
                      <p className="text-sm font-medium">No messages yet</p>
                    </div>
                  )}
                  {participantMessages.map(msg => (
                    <div
                      key={msg.id}
                      ref={node => { participantMessageRefs.current[msg.id] = node; }}
                      className={cn(
                        'rounded-2xl transition-[background-color,box-shadow] duration-1000',
                        highlightedMessageId === msg.id && 'bg-primary/5 ring-2 ring-primary/50',
                      )}
                    >
                      <ChatMessageBubble
                        message={msg}
                        conversationId={chatPanel.conversationId}
                        onSuggestionClick={(sid) => openChat({ suggestionId: sid })}
                      />
                    </div>
                  ))}
                </div>
              </ScrollArea>
              {isAutonomous ? (
                <div className="flex items-center gap-2 px-4 py-3 border-t bg-muted/30 shrink-0">
                  <p className="flex-1 text-xs text-muted-foreground">RentMate is chatting on your behalf.</p>
                </div>
              ) : (
                <div className="border-t shrink-0">
                  {shouldShowActiveTaskSuggestion && activeSuggestion && activeSuggestion.status === 'pending' && (
                    <div className="border-b bg-muted/20 p-3">
                      {activeMessageSuggestion && activeMessageSuggestionSendAction ? (
                        <MessageSuggestionCard
                          suggestion={activeMessageSuggestion}
                          sendActionLabel={
                            activeMessageSuggestion.options?.find((item) => item.action === activeMessageSuggestionSendAction)?.label ??
                            'Send'
                          }
                          disabled={isTyping}
                          onAccept={async (action) => {
                            const result = await actOnSuggestion(activeMessageSuggestion.id, action);
                            const { status } = result.actOnSuggestion;
                            updateSuggestionStatus(activeMessageSuggestion.id, status.toLowerCase() as 'accepted' | 'dismissed');
                            refreshData();
                          }}
                          onSendEdited={async (body) => {
                            const result = await actOnSuggestion(activeMessageSuggestion.id, 'edit_message', body);
                            const { status } = result.actOnSuggestion;
                            updateSuggestionStatus(activeMessageSuggestion.id, status.toLowerCase() as 'accepted' | 'dismissed');
                            refreshData();
                          }}
                          onDismiss={async () => {
                            const result = await actOnSuggestion(activeMessageSuggestion.id, 'reject_task');
                            const { status } = result.actOnSuggestion;
                            updateSuggestionStatus(activeMessageSuggestion.id, status.toLowerCase() as 'accepted' | 'dismissed');
                            refreshData();
                          }}
                        />
                      ) : (
                        <SuggestionOptions
                          options={activeSuggestion.options}
                          onAction={async (action) => {
                            if (action === 'request_file_upload') {
                              chatInputRef.current?.triggerFileUpload();
                              toast.info('Upload the requested file in this task chat.');
                              return;
                            }
                            const result = await actOnSuggestion(activeSuggestion.id, action);
                            const { status } = result.actOnSuggestion;
                            updateSuggestionStatus(activeSuggestion.id, status.toLowerCase() as 'accepted' | 'dismissed');
                            refreshData();
                          }}
                        />
                      )}
                    </div>
                  )}
                  <ChatInput
                    onSend={async (content) => {
                      const taskId = chatPanel.taskId!;
                      const msg: ChatMessage = {
                        id: `msg-${Date.now()}`,
                        role: 'user',
                        content,
                        timestamp: new Date(),
                        senderName: 'You',
                        senderType: 'manager',
                        messageType: 'message',
                      };
                      setParticipantMessages(prev => [...prev, msg]);
                      try {
                        await sendMessage({ conversationId: lc.uid, body: content });
                      } catch {
                        toast.error('Failed to send message');
                      }
                    }}
                    placeholder={`Reply in ${lc.label} chat...`}
                    lastSentMessage={getLastSentMessage(participantMessages)}
                  />
                </div>
              )}
            </TabsContent>
          ))}
        </Tabs>
      ) : (
        <>
          {/* Linked-task header — visible whenever the open conversation
              belongs to a task, so the manager can jump back to the task
              page from the embedded chat panel. */}
          {convTaskLink && (
            <Link
              to={`/tasks/${convTaskLink.taskId}`}
              className="flex items-center gap-2 px-3 py-1.5 border-b bg-muted/40 hover:bg-muted/60 transition-colors shrink-0"
              title={convTaskLink.taskTitle ?? `Open task #${convTaskLink.taskId}`}
            >
              <ClipboardList className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
              <span className="text-[11px] font-medium text-foreground truncate">
                Task #{convTaskLink.taskId}
                {convTaskLink.taskTitle ? <span className="text-muted-foreground"> — {convTaskLink.taskTitle}</span> : null}
              </span>
              <LinkIcon className="h-3 w-3 text-muted-foreground ml-auto shrink-0" />
            </Link>
          )}
          {/* Onboarding progress strip */}
          {onboarding.isActive && onboarding.state && (
            <OnboardingProgress steps={onboarding.state.steps} onDismiss={onboarding.dismiss} />
          )}
          {/* Messages (non-task) */}
          <ScrollArea className="flex-1 min-w-0 overflow-x-hidden" ref={scrollRef}>
            <div className="w-full min-w-0 max-w-full space-y-4 overflow-x-hidden p-4">
              {messages.length === 0 && !isTyping && !onboarding.isActive && (
                <div className="text-center py-8 text-muted-foreground">
                  <Bot className="h-8 w-8 mx-auto mb-2 opacity-40" />
                  <p className="text-sm font-medium">
                    {activeSuggestion
                      ? 'Discuss this suggestion with AI'
                      : 'Ask me anything about your properties'
                    }
                  </p>
                  <p className="text-xs mt-1">
                    {activeSuggestion
                      ? 'Try: "Can we lower this?" or "Make it more friendly"'
                      : 'Try: "What\'s my occupancy rate?" or "Draft a notice"'
                    }
                  </p>
                </div>
              )}
              {renderedItems.map(item =>
                item.kind === 'msg' ? (
                  <ChatMessageBubble
                    key={item.msg.id}
                    message={item.msg}
                    conversationId={chatPanel.conversationId}
                    questionAnsweredByContent={questionAnswers.get(item.msg.id) ?? null}
                    onSuggestionClick={(sid) => openChat({ suggestionId: sid })}
                  />
                ) : null
              )}
              {isTyping && (
                <div data-testid="thinking-row" className="flex items-start gap-2 overflow-hidden text-muted-foreground">
                  <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary/10 shrink-0 mt-0.5">
                    <Bot className="h-3.5 w-3.5 text-primary" />
                  </div>
                  <div data-testid="thinking-bubble" className="flex-1 min-w-0 overflow-hidden py-2 px-3 rounded-2xl bg-muted">
                    {progressLog.length === 0 ? (
                      <div className="flex gap-1 py-0.5">
                        <span className="w-1.5 h-1.5 bg-muted-foreground/50 rounded-full animate-bounce [animation-delay:0ms]" />
                        <span className="w-1.5 h-1.5 bg-muted-foreground/50 rounded-full animate-bounce [animation-delay:150ms]" />
                        <span className="w-1.5 h-1.5 bg-muted-foreground/50 rounded-full animate-bounce [animation-delay:300ms]" />
                      </div>
                    ) : (
                      <div className="space-y-0.5 overflow-hidden">
                        {progressLog.slice(-3).map((line, i, arr) => (
                          <p
                            key={i}
                            data-testid="progress-line"
                            className={`text-[11px] font-mono break-all overflow-hidden ${
                              i === arr.length - 1
                                ? 'text-foreground/80'
                                : 'text-muted-foreground/50'
                            }`}
                          >
                            {line}
                          </p>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
              {/* Onboarding chips — show after first AI message when no path picked */}
              {onboarding.isActive && !onboarding.state?.path_picked && !isTyping &&
                messages.length > 0 && messages[messages.length - 1].role === 'assistant' && (
                <OnboardingChips
                  llmNotConfigured={!onboarding.llmConfigured}
                  onSelect={(choice) => {
                    if (choice === 'configure_llm') {
                      window.location.href = '/settings';
                      return;
                    }
                    if (choice === 'upload') {
                      chatInputRef.current?.triggerFileUpload();
                      return;
                    }
                    const textMap: Record<OnboardingChoice, string> = {
                      upload: '',
                      manual: 'I want to add a property manually',
                      prose: 'Let me tell you about my portfolio',
                      skip: "I'll skip for now and explore on my own",
                      configure_llm: '',
                    };
                    handleSend(textMap[choice]);
                  }}
                  disabled={isTyping}
                />
              )}
            </div>
          </ScrollArea>
          {activeSuggestion && activeSuggestion.status === 'pending' ? (
            <>
              {activeMessageSuggestion && activeMessageSuggestionSendAction ? (
                <MessageSuggestionCard
                  suggestion={activeMessageSuggestion}
                  sendActionLabel={
                    activeMessageSuggestion.options?.find((item) => item.action === activeMessageSuggestionSendAction)?.label ??
                    'Send'
                  }
                  disabled={isTyping}
                  onAccept={async (action) => {
                    const result = await actOnSuggestion(activeMessageSuggestion.id, action);
                    const { status } = result.actOnSuggestion;
                    updateSuggestionStatus(activeMessageSuggestion.id, status.toLowerCase() as 'accepted' | 'dismissed');
                    refreshData();
                  }}
                  onSendEdited={async (body) => {
                    const result = await actOnSuggestion(activeMessageSuggestion.id, 'edit_message', body);
                    const { status } = result.actOnSuggestion;
                    updateSuggestionStatus(activeMessageSuggestion.id, status.toLowerCase() as 'accepted' | 'dismissed');
                    refreshData();
                  }}
                  onDismiss={async () => {
                    const result = await actOnSuggestion(activeMessageSuggestion.id, 'reject_task');
                    const { status } = result.actOnSuggestion;
                    updateSuggestionStatus(activeMessageSuggestion.id, status.toLowerCase() as 'accepted' | 'dismissed');
                    refreshData();
                  }}
                />
              ) : (
                <SuggestionOptions
                  options={activeSuggestion.options}
                  onAction={async (action) => {
                    if (action === 'request_file_upload') {
                      chatInputRef.current?.triggerFileUpload();
                      toast.info('Upload the requested file in this task chat.');
                      return;
                    }
                    const result = await actOnSuggestion(activeSuggestion.id, action);
                    const { status } = result.actOnSuggestion;
                    updateSuggestionStatus(activeSuggestion.id, status.toLowerCase() as 'accepted' | 'dismissed');
                    closeChat();
                  }}
                />
              )}
              {isReadOnlyConv ? (
                <div className="px-4 py-3 text-xs text-muted-foreground bg-muted/40 border-t flex items-center gap-2">
                  <span className="font-medium">Read-only.</span>
                  <span>This thread is mirrored from another platform — send your reply there.</span>
                </div>
              ) : (
                <ChatInput ref={chatInputRef} onSend={handleSend} onInsertCleared={handleInsertCleared} placeholder={placeholder} lastSentMessage={lastSentMessage} disabled={isTyping} uploadFile={uploadFile} attachments={pendingAttachments} setAttachments={setPendingAttachments} />
              )}
            </>
          ) : (
            <ChatInput ref={chatInputRef} onSend={handleSend} onInsertCleared={handleInsertCleared} placeholder={placeholder} lastSentMessage={lastSentMessage} disabled={isTyping} uploadFile={uploadFile} attachments={pendingAttachments} setAttachments={setPendingAttachments} />
          )}
        </>
      )}
    </div>
  );
}
