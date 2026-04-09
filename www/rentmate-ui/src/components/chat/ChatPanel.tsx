import { useRef, useEffect, useMemo, useState } from 'react';
import { X, Bot, Sparkles, Users, Zap, ShieldCheck, Hand, Lock, MessageSquare, RotateCcw, Loader2, Trash2, Link as LinkIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ChatMessageBubble } from './ChatMessage';
import { ChatInput, ChatInputHandle, PendingAttachment } from './ChatInput';
import { useApp } from '@/context/AppContext';
import { ActionDeskTask, ChatMessage, LinkedConversation, ManagedDocument, categoryLabels, TaskMode } from '@/data/mockData';
import { getToken, authFetch } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import { SuggestionOptions } from './SuggestionOptions';
import { ProgressSteps } from './ProgressSteps';
import { OnboardingChips, OnboardingChoice } from './OnboardingChips';
import { OnboardingProgress } from './OnboardingProgress';
import { useOnboarding } from '@/hooks/useOnboarding';
import { graphqlQuery, TASK_QUERY, SEND_MESSAGE_MUTATION, DELETE_TASK_MUTATION, CONVERSATION_MESSAGES_QUERY } from '@/data/api';
import { apiMessagesToChatThread } from '@/hooks/useApiData';

function authHeaders() {
  const t = getToken();
  return { 'Content-Type': 'application/json', ...(t ? { Authorization: `Bearer ${t}` } : {}) };
}

function autonomousBlockReason(_task: ActionDeskTask): string | null {
  return null;
}

const modeConfig: Record<TaskMode, { label: string; icon: React.ElementType; className: string }> = {
  autonomous: { label: 'Autonomous', icon: Zap, className: 'bg-accent/15 text-accent' },
  waiting_approval: { label: 'Needs Approval', icon: ShieldCheck, className: 'bg-warning/15 text-warning-foreground' },
  manual: { label: 'Manual', icon: Hand, className: 'bg-muted text-muted-foreground' },
};

function getModeBadge(task: { mode: TaskMode; participants: { type: string }[] }) {
  if (task.mode === 'manual') {
    const hasExternal = task.participants.some(p => p.type === 'tenant' || p.type === 'vendor');
    if (!hasExternal) return { label: 'Agent', icon: Bot, className: 'bg-primary/10 text-primary' };
  }
  return modeConfig[task.mode];
}

export function ChatPanel({ embedded = false }: { embedded?: boolean } = {}) {
  const { chatPanel, closeChat, openChat, setChatConversationId, suggestions, actionDeskTasks, addChatMessage, updateTaskMessage, setTaskMessages, updateTask, removeTask, updateSuggestionStatus, addDocument, replaceDocument, removeDocument, refreshData } = useApp();
  const [dismissConfirm, setDismissConfirm] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [activeTaskTab, setActiveTaskTab] = useState<string>('ai');
  const [participantMessages, setParticipantMessages] = useState<ChatMessage[]>([]);
  const [participantLoading, setParticipantLoading] = useState(false);
  const onboarding = useOnboarding();
  const onboardingStartedRef = useRef(false);

  const handleDismiss = async () => {
    if (!chatPanel.taskId) return;
    if (!dismissConfirm) {
      setDismissConfirm(true);
      return;
    }
    const taskId = chatPanel.taskId;
    setDismissing(true);
    try {
      await graphqlQuery<unknown>(`mutation { updateTaskStatus(uid: "${taskId}", status: "dismissed") { uid } }`);
      if (activeTask?.aiConversationId) {
        await graphqlQuery(SEND_MESSAGE_MUTATION, {
          input: { conversationId: activeTask.aiConversationId, body: 'Task dismissed — this item will not be re-created by automations.', messageType: 'internal', senderName: 'RentMate', isAi: true },
        });
      }
      addChatMessage({ taskId }, { id: `dismiss-${Date.now()}`, role: 'assistant', content: 'Task dismissed — this item will not be re-created by automations.', timestamp: new Date(), senderName: 'RentMate', messageType: 'internal' });
      updateTask(taskId, { status: 'cancelled' });
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
      await graphqlQuery(DELETE_TASK_MUTATION, { uid: taskId });
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
    setActiveTaskTab('ai');
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

  const activeTask = useMemo(() =>
    chatPanel.taskId ? actionDeskTasks.find(t => t.id === chatPanel.taskId) : null,
    [chatPanel.taskId, actionDeskTasks]
  );

  // DB-backed conversation messages (for conversationId-based chats)
  const [convMessages, setConvMessages] = useState<ChatMessage[]>([]);
  const activeConversationId = chatPanel.conversationId;

  useEffect(() => {
    if (activeTask || activeSuggestion) { setConvMessages([]); return; }
    if (!activeConversationId) { setConvMessages([]); onboardingStartedRef.current = false; return; }
    graphqlQuery<{ conversationMessages: Array<{ uid: string; body: string; messageType: string; senderName: string; senderType: string | null; isAi: boolean; isSystem: boolean; draftReply?: string; suggestionId?: string; sentAt: string }> }>(
      CONVERSATION_MESSAGES_QUERY, { uid: activeConversationId }
    ).then(result => {
      setConvMessages((result.conversationMessages ?? []).map(m => ({
        id: m.uid,
        role: m.isAi ? 'assistant' as const : 'user' as const,
        content: m.body,
        timestamp: new Date(m.sentAt),
        senderName: m.senderName,
        senderType: m.isAi ? 'ai' as const : 'manager' as const,
        messageType: m.messageType as ChatMessage['messageType'],
        draftReply: m.draftReply ?? undefined,
        suggestionId: m.suggestionId ?? undefined,
      })));
    }).catch(() => {});
  }, [activeConversationId, activeTask, activeSuggestion]);

  const messages = activeTask
    ? activeTask.chatThread
    : activeSuggestion
      ? activeSuggestion.chatThread
      : convMessages;

  const isAutonomous = activeTask?.mode === 'autonomous';

  // Build flat list with section dividers inserted at internal↔external transitions.
  const renderedItems = useMemo(() => {
    type Item =
      | { kind: 'msg'; msg: typeof messages[0] }
      | { kind: 'divider'; label: string; key: string };
    const result: Item[] = [];
    let lastWasExternal: boolean | null = null;
    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i];
      const st = msg.senderType ?? (msg.role === 'assistant' ? 'ai' : 'manager');
      const isExternal = st === 'tenant' || st === 'vendor';
      if (lastWasExternal !== null && isExternal !== lastWasExternal) {
        result.push({
          kind: 'divider',
          label: isExternal ? 'External conversation' : 'Internal thread',
          key: `divider-${i}`,
        });
      }
      lastWasExternal = isExternal;
      result.push({ kind: 'msg', msg });
    }
    return result;
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
    // ScrollArea renders a viewport child — scroll that instead of the wrapper
    const el = scrollRef.current;
    if (!el) return;
    const viewport = el.querySelector('[data-radix-scroll-area-viewport]') as HTMLElement | null;
    const target = viewport ?? el;
    setTimeout(() => { target.scrollTop = target.scrollHeight; }, 50);
  }, [messages, convMessages, participantMessages, isTyping, progressLog]);

  // Refresh task messages from DB whenever a task is opened + poll for new ones
  const loadTaskMessages = (taskId: string) => {
    graphqlQuery<{ task: { messages: Parameters<typeof apiMessagesToChatThread>[0] } | null }>(
      TASK_QUERY, { uid: taskId }
    ).then(result => {
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
    graphqlQuery<{ conversationMessages: Array<{ uid: string; body: string; messageType: string; senderName: string; senderType: string | null; isAi: boolean; isSystem: boolean; sentAt: string }> }>(
      CONVERSATION_MESSAGES_QUERY, { uid: convoId }
    ).then(result => {
      const msgs: ChatMessage[] = (result.conversationMessages ?? []).map(m => {
        let st: ChatMessage['senderType'] = 'manager';
        if (m.isAi) st = 'ai';
        else if (m.senderType === 'external_contact') st = 'vendor';
        else if (m.senderType === 'tenant') st = 'tenant';
        return {
          id: m.uid,
          role: m.isAi ? 'assistant' as const : 'user' as const,
          content: m.body,
          timestamp: new Date(m.sentAt),
          senderName: m.senderName,
          senderType: st,
          messageType: m.messageType as ChatMessage['messageType'],
        };
      });
      setParticipantMessages(msgs);
    }).catch(() => {}).finally(() => { if (showLoading) setParticipantLoading(false); });
  };

  // Determine the non-AI linked conversations
  const linkedChats: LinkedConversation[] = (activeTask?.linkedConversations ?? []).filter(
    lc => lc.conversationType !== 'task_ai' && lc.conversationType !== 'suggestion_ai'
  );

  // Load participant messages when a linked conversation tab is active
  useEffect(() => {
    if (activeTaskTab === 'ai' || !activeTaskTab) return;
    // activeTaskTab is a conversation UID
    const convoId = activeTaskTab;
    loadParticipantMessages(convoId, true);
    const interval = setInterval(() => loadParticipantMessages(convoId), 5000);
    return () => clearInterval(interval);
  }, [activeTaskTab]); // eslint-disable-line react-hooks/exhaustive-deps

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
        const res = await fetch(`/chat/stream/${streamId}`, {
          headers: authHeaders(),
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
                if (event.suggestion_messages) {
                  for (const sm of event.suggestion_messages as Array<{ id: string; body: string; suggestion_id?: string }>) {
                    addChatMessage({ taskId }, {
                      id: sm.id, role: 'assistant', content: sm.body,
                      timestamp: new Date(), senderName: 'RentMate',
                      senderType: 'ai', messageType: 'suggestion',
                      suggestionId: sm.suggestion_id,
                    });
                  }
                }
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
            graphqlQuery<{ task: { messages: Parameters<typeof apiMessagesToChatThread>[0] } | null }>(
              TASK_QUERY, { uid: taskId }
            ).then(result => {
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

      const res = await fetch('/chat/send', {
        method: 'POST',
        headers: authHeaders(),
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
          let event: { type: string; text?: string; reply?: string; stream_id?: string; message?: string; conversation_id?: string; suggestion_messages?: Array<{ id: string; body: string; suggestion_id?: string }>; onboarding?: Parameters<typeof onboarding.update>[0] };
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
            // Append any suggestion messages the agent created (flushed
            // after the AI reply so they appear below it).
            if (event.suggestion_messages) {
              for (const sm of event.suggestion_messages as Array<{ id: string; body: string; suggestion_id?: string }>) {
                const sugMsg: ChatMessage = {
                  id: sm.id,
                  role: 'assistant',
                  content: sm.body,
                  timestamp: new Date(),
                  senderName: 'RentMate',
                  senderType: 'ai',
                  messageType: 'suggestion',
                  suggestionId: sm.suggestion_id,
                };
                if (!taskId && !suggestionId) {
                  setConvMessages(prev => [...prev, sugMsg]);
                } else {
                  addChatMessage({ taskId, suggestionId }, sugMsg);
                }
              }
            }
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
          graphqlQuery<{ task: { messages: Parameters<typeof apiMessagesToChatThread>[0] } | null }>(
            TASK_QUERY, { uid: taskId }
          ).then(result => {
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

  return (
    <div className={cn(
      "flex flex-col overflow-hidden bg-card",
      embedded
        ? "h-full w-full"
        : "fixed inset-0 z-50 md:static md:inset-auto md:z-auto md:w-[320px] lg:w-[380px] md:border-l md:shrink-0 md:h-full"
    )}>
      {/* Header */}
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

      {/* Task Context */}
      {activeTask && (
        <div className="p-3 border-b bg-muted/30 space-y-2">
          {activeTask.confidential && (
            <div className="flex items-center gap-2 rounded-lg bg-destructive/10 border border-destructive/20 px-3 py-2">
              <Lock className="h-3.5 w-3.5 text-destructive shrink-0" />
              <p className="text-[11px] text-destructive font-medium">
                This is a confidential thread. The complainant's identity is not shared with other parties.
              </p>
            </div>
          )}
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 flex-wrap">
              {(() => {
                const mode = getModeBadge(activeTask);
                const ModeIcon = mode.icon;
                return (
                  <Badge variant="secondary" className={cn('text-[10px] rounded-lg gap-1', mode.className)}>
                    <ModeIcon className="h-3 w-3" />
                    {mode.label}
                  </Badge>
                );
              })()}
            </div>
            {activeTask.mode === 'autonomous' && chatPanel.taskId && (
              <Button
                size="sm"
                variant="outline"
                className="h-6 rounded-lg text-[10px] px-2 gap-1 hover:bg-destructive/10 hover:text-destructive hover:border-destructive/30"
                onClick={() => {
                  updateTask(chatPanel.taskId!, { mode: 'manual' });
                  addChatMessage(
                    { taskId: chatPanel.taskId },
                    { id: `msg-${Date.now()}`, role: 'assistant', content: 'RentMate turned off for this task. You\'re now in full manual control.', timestamp: new Date(), senderName: 'RentMate', messageType: 'internal' }
                  );
                }}
              >
                <Zap className="h-3 w-3" />
                Turn off RentMate
              </Button>
            )}
            {activeTask.mode === 'manual' && chatPanel.taskId && (() => {
              const blockReason = autonomousBlockReason(activeTask);
              return blockReason ? (
                <span className="text-[10px] text-muted-foreground italic">{blockReason}</span>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 rounded-lg text-[10px] px-2 gap-1 hover:bg-accent/10 hover:text-accent hover:border-accent/30"
                  onClick={async () => {
                    const taskId = chatPanel.taskId!;
                    updateTask(taskId, { mode: 'autonomous' });
                    // Assess the conversation — the agent will respond only if warranted
                    setIsTyping(true);
                    setProgressLog([]);
                    try {
                      const res = await fetch('/chat/assess', {
                        method: 'POST',
                        headers: authHeaders(),
                        body: JSON.stringify({ task_id: taskId }),
                      });
                      if (!res.ok) { setIsTyping(false); return; }
                      const reader = res.body!.getReader();
                      const decoder = new TextDecoder();
                      let buf = '';
                      const progressLines: string[] = [];
                      while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;
                        buf += decoder.decode(value, { stream: true });
                        const lines = buf.split('\n');
                        buf = lines.pop() ?? '';
                        for (const line of lines) {
                          if (!line.startsWith('data: ')) continue;
                          let event: { type: string; text?: string; reply?: string | null; message_id?: string | null; conversation_id?: string; suggestion_messages?: Array<{ id: string; body: string; suggestion_id?: string }> };
                          try { event = JSON.parse(line.slice(6)); } catch { continue; }
                          if (event.type === 'progress') {
                            progressLines.push(event.text as string);
                            setProgressLog(prev => [...prev, event.text as string]);
                          } else if (event.type === 'done') {
                            // Reasoning traces go to AI thread (internal)
                            const traceLines = progressLines.filter(l => l !== 'Thinking\u2026');
                            if (traceLines.length > 0) {
                              addChatMessage({ taskId }, {
                                id: `thinking-${Date.now()}`, role: 'assistant',
                                content: traceLines.join('\n'), timestamp: new Date(),
                                senderName: 'RentMate', senderType: 'ai', messageType: 'internal',
                              });
                            }
                            // Reply goes to AI thread — external messages
                            // are sent via the message_person suggestion flow
                            if (event.reply) {
                              addChatMessage({ taskId }, {
                                id: event.message_id || `msg-${Date.now()}`, role: 'assistant',
                                content: event.reply, timestamp: new Date(),
                                senderName: 'RentMate', senderType: 'ai', messageType: 'message',
                              });
                            }
                            if (event.suggestion_messages) {
                              for (const sm of event.suggestion_messages) {
                                addChatMessage({ taskId }, {
                                  id: sm.id, role: 'assistant', content: sm.body,
                                  timestamp: new Date(), senderName: 'RentMate',
                                  senderType: 'ai', messageType: 'suggestion', suggestionId: sm.suggestion_id,
                                });
                              }
                            }
                            refreshData();
                          }
                        }
                      }
                    } catch (err) {
                      console.warn('[assess] failed:', err);
                    } finally {
                      setIsTyping(false);
                    }
                  }}
                >
                  <Zap className="h-3 w-3" />
                  Turn on RentMate
                </Button>
              );
            })()}
          </div>
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
          value={activeTaskTab}
          onValueChange={v => setActiveTaskTab(v)}
          className="flex-1 flex flex-col min-h-0"
        >
          <TabsList className="shrink-0 mx-3 mt-2 mb-0 h-8 self-start gap-1 bg-muted/50">
            <TabsTrigger value="ai" className="text-xs h-6 px-3">AI</TabsTrigger>
            {linkedChats.map(lc => (
              <TabsTrigger key={lc.uid} value={lc.uid} className="text-xs h-6 px-3">
                {lc.label}
                {lc.messageCount > 0 && (
                  <span className="ml-1 text-[9px] text-muted-foreground">{lc.messageCount}</span>
                )}
              </TabsTrigger>
            ))}
            <TabsTrigger value="progress" className="text-xs h-6 px-3">Progress</TabsTrigger>
          </TabsList>

          {/* AI tab — internal RentMate thread */}
          <TabsContent value="ai" className="hidden data-[state=active]:flex flex-1 flex-col min-h-0 mt-0">
            {/* AI participant chip */}
            <div className="flex items-center gap-1.5 px-3 py-1.5 border-b bg-muted/20 shrink-0">
              <Badge variant="secondary" className="text-[10px] rounded-lg gap-1 bg-primary/10 text-primary">
                <Bot className="h-3 w-3" />
                RentMate AI
              </Badge>
            </div>
            <ScrollArea className="flex-1 overflow-x-hidden" ref={scrollRef}>
              <div className="p-4 space-y-4 w-full overflow-x-hidden">
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
                    await graphqlQuery<unknown>(`mutation { updateTaskStatus(uid: "${taskId}", status: "active") { uid } }`);
                    if (activeTask?.aiConversationId) await graphqlQuery(SEND_MESSAGE_MUTATION, {
                      input: { conversationId: activeTask.aiConversationId, body: 'Task re-opened.', messageType: 'internal', senderName: 'RentMate', isAi: true },
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
                <ChatInput ref={chatInputRef} onSend={handleSend} onInsertCleared={handleInsertCleared} placeholder={placeholder} disabled={isTyping} uploadFile={uploadFile} attachments={pendingAttachments} setAttachments={setPendingAttachments} />
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
                    <ChatMessageBubble key={msg.id} message={msg} onSuggestionClick={(sid) => openChat({ suggestionId: sid })} />
                  ))}
                </div>
              </ScrollArea>
              {isAutonomous ? (
                <div className="flex items-center gap-2 px-4 py-3 border-t bg-muted/30 shrink-0">
                  <Zap className="h-4 w-4 text-accent shrink-0" />
                  <p className="flex-1 text-xs text-muted-foreground">RentMate is chatting on your behalf.</p>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 rounded-lg text-[11px] px-2.5 shrink-0"
                    onClick={() => {
                      updateTask(chatPanel.taskId!, { mode: 'manual' });
                      addChatMessage({ taskId: chatPanel.taskId }, { id: `msg-${Date.now()}`, role: 'assistant', content: 'Switched to manual — you\'re in control now.', timestamp: new Date(), senderName: 'RentMate', messageType: 'internal' });
                    }}
                  >
                    Take control
                  </Button>
                </div>
              ) : (
                <div className="border-t shrink-0">
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
                        await graphqlQuery(SEND_MESSAGE_MUTATION, {
                          input: { conversationId: lc.uid, body: content },
                        });
                      } catch {
                        toast.error('Failed to send message');
                      }
                    }}
                    placeholder={`Reply in ${lc.label} chat...`}
                  />
                </div>
              )}
            </TabsContent>
          ))}
        </Tabs>
      ) : (
        <>
          {/* Onboarding progress strip */}
          {onboarding.isActive && onboarding.state && (
            <OnboardingProgress steps={onboarding.state.steps} onDismiss={onboarding.dismiss} />
          )}
          {/* Messages (non-task) */}
          <ScrollArea className="flex-1 overflow-x-hidden" ref={scrollRef}>
            <div className="p-4 space-y-4 w-full overflow-x-hidden">
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
                item.kind === 'divider' ? (
                  <div key={item.key} className="flex items-center gap-2 py-1">
                    <div className="flex-1 h-px bg-border/60" />
                    <span className="flex items-center gap-1 text-[10px] text-muted-foreground font-medium">
                      <MessageSquare className="h-2.5 w-2.5" />
                      {item.label}
                    </span>
                    <div className="flex-1 h-px bg-border/60" />
                  </div>
                ) : (
                  <ChatMessageBubble
                    key={item.msg.id}
                    message={item.msg}
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
            <SuggestionOptions
              options={activeSuggestion.options}
              onAction={async (action) => {
                const { graphqlQuery: gql, ACT_ON_SUGGESTION_MUTATION } = await import('@/data/api');
                const result = await gql<{ actOnSuggestion: { uid: string; status: string; taskId?: string } }>(
                  ACT_ON_SUGGESTION_MUTATION,
                  { uid: activeSuggestion.id, action },
                );
                const { status } = result.actOnSuggestion;
                updateSuggestionStatus(activeSuggestion.id, status as 'accepted' | 'dismissed');
                closeChat();
              }}
            />
          ) : (
            <ChatInput ref={chatInputRef} onSend={handleSend} onInsertCleared={handleInsertCleared} placeholder={placeholder} disabled={isTyping} uploadFile={uploadFile} attachments={pendingAttachments} setAttachments={setPendingAttachments} />
          )}
        </>
      )}
    </div>
  );
}
