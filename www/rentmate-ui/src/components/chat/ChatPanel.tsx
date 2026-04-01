import { useRef, useEffect, useMemo, useState } from 'react';
import { X, Bot, Sparkles, Users, Zap, ShieldCheck, Hand, Lock, MessageSquare, RotateCcw, Loader2, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ChatMessageBubble } from './ChatMessage';
import { ChatInput, ChatInputHandle } from './ChatInput';
import { useApp } from '@/context/AppContext';
import { ActionDeskTask, ChatMessage, ManagedDocument, categoryLabels, TaskMode } from '@/data/mockData';
import { getToken, authFetch } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import { AgentTaskProposal, AgentProposedTask } from './AgentTaskProposal';
import { AgentActionConfirm, AgentProposedAction } from './AgentActionConfirm';
import { SuggestionOptions } from './SuggestionOptions';
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

export function ChatPanel() {
  const { chatPanel, closeChat, suggestions, actionDeskTasks, addChatMessage, updateTaskMessage, setTaskMessages, updateTask, removeTask, updateSuggestionStatus, chatSessions, addDocument, replaceDocument, removeDocument } = useApp();
  const [dismissConfirm, setDismissConfirm] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [activeTaskTab, setActiveTaskTab] = useState<'chat' | 'ai'>('chat');
  const [participantMessages, setParticipantMessages] = useState<ChatMessage[]>([]);
  const [participantLoading, setParticipantLoading] = useState(false);

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
    setActiveTaskTab('chat');
    setParticipantMessages([]);
  }, [chatPanel.taskId]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const chatInputRef = useRef<ChatInputHandle>(null);
  const [isTyping, setIsTyping] = useState(false);
  const [progressLog, setProgressLog] = useState<string[]>([]);
  const [proposals, setProposals] = useState<AgentProposedTask[]>([]);
  const [actionProposals, setActionProposals] = useState<AgentProposedAction[]>([]);

  // Clear proposals when switching tasks so follow-ups from other tasks don't bleed through
  useEffect(() => {
    setProposals([]);
    setActionProposals([]);
  }, [chatPanel.taskId]);

  const activeSuggestion = useMemo(() =>
    chatPanel.suggestionId ? suggestions.find(s => s.id === chatPanel.suggestionId) : null,
    [chatPanel.suggestionId, suggestions]
  );

  const activeTask = useMemo(() =>
    chatPanel.taskId ? actionDeskTasks.find(t => t.id === chatPanel.taskId) : null,
    [chatPanel.taskId, actionDeskTasks]
  );

  const activeSession = useMemo(() =>
    chatPanel.sessionId ? chatSessions.find(s => s.id === chatPanel.sessionId) : null,
    [chatPanel.sessionId, chatSessions]
  );

  const messages = activeTask
    ? activeTask.chatThread
    : activeSuggestion
      ? activeSuggestion.chatThread
      : (activeSession?.messages ?? []);

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
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isTyping]);

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

  useEffect(() => {
    const parentId = activeTask?.parentConversationId;
    if (activeTaskTab !== 'chat' || !parentId) return;
    loadParticipantMessages(parentId, true);
    const interval = setInterval(() => loadParticipantMessages(parentId), 5000);
    return () => clearInterval(interval);
  }, [activeTaskTab, activeTask?.parentConversationId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reconnect to an in-flight agent task when the chat panel opens.
  // If the agent is still running, stream its remaining progress so the user
  // sees the thinking indicator and receives the reply when it finishes.
  useEffect(() => {
    if (!chatPanel.taskId) return;
    const taskId = chatPanel.taskId;
    const controller = new AbortController();

    (async () => {
      try {
        const res = await fetch(`/chat/task/${taskId}/stream`, {
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
                addAiMessage(event.reply, { taskId });
                handleAgentActions(event.actions ?? [], taskId);
                setIsTyping(false);
              } else if (event.type === 'error') {
                toast.error('Agent encountered an error');
                setIsTyping(false);
              }
            } catch { /* ignore malformed lines */ }
          }
        }

        // Stream ended without `done` — agent may have finished after a reconnect
        // gap; pull the latest messages from DB to show any persisted reply.
        if (active && !reconnectDone) {
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

  const addAiMessage = (content: string, context: { taskId?: string | null; suggestionId?: string | null; sessionId?: string | null }) => {
    addChatMessage(context, {
      id: `msg-${Date.now()}`,
      role: 'assistant',
      content,
      timestamp: new Date(),
      senderName: 'RentMate',
      senderType: 'ai',
      messageType: 'message',
    });
  };

  const handleAgentActions = (actions: Array<{ action: string; [key: string]: unknown }>, taskId?: string | null) => {
    for (const act of actions) {
      if (act.action === 'propose_task') {
        setProposals(prev => [...prev, {
          _proposalId: `${Date.now()}-${Math.random()}`,
          title: act.title as string,
          category: act.category as string,
          urgency: (act.urgency as string) ?? 'medium',
          description: (act.description as string) || undefined,
          propertyId: (act.property_id as string) || undefined,
        }]);
      } else if (act.action === 'close_task_proposed') {
        const tid = (act.task_id as string) || taskId;
        if (tid) {
          setActionProposals(prev => [...prev, {
            _proposalId: `${Date.now()}-${Math.random()}`,
            action: 'close_task',
            taskId: tid,
          }]);
        }
      } else if (act.action === 'set_mode_proposed') {
        const tid = (act.task_id as string) || taskId;
        if (tid) {
          setActionProposals(prev => [...prev, {
            _proposalId: `${Date.now()}-${Math.random()}`,
            action: 'set_mode',
            taskId: tid,
            mode: act.mode as 'autonomous' | 'manual' | 'waiting_approval',
          }]);
        }
      }
      // task_closed and mode_changed are no longer sent — writes now go through HITL above
    }
  };

  const callAI = async (userMessage: string) => {
    // Capture IDs immediately — async operations below must not use stale closures
    const taskId = chatPanel.taskId;
    const suggestionId = chatPanel.suggestionId;
    const sessionId = chatPanel.sessionId;

    setIsTyping(true);
    setProgressLog([]);
    try {
      if (taskId) {
        // Task thread — SSE stream with live progress.
        // progressLines accumulates the full trace so we can persist it as an
        // internal message when done — needed on iOS Safari where the browser
        // buffers the entire SSE response and delivers it all at once, making
        // live state updates invisible (they're batched with the cleanup).
        const progressLines: string[] = [];

        const res = await fetch('/chat/task', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ task_id: taskId, message: userMessage }),
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

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';
          let sseError: Error | null = null;
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            let event: { type: string; text?: string; reply?: string; actions?: Array<Record<string, unknown>>; message?: string };
            try {
              event = JSON.parse(line.slice(6));
            } catch {
              continue; // ignore malformed lines
            }
            if (event.type === 'progress') {
              progressLines.push(event.text as string);
              setProgressLog(prev => [...prev, event.text as string]);
            } else if (event.type === 'done') {
              receivedDone = true;
              // Persist reasoning trace as an internal message so it's visible
              // even when the live indicator was never rendered (iOS Safari).
              if (progressLines.length > 0) {
                addChatMessage({ taskId }, {
                  id: `thinking-${Date.now()}`,
                  role: 'assistant',
                  content: progressLines.join('\n'),
                  timestamp: new Date(),
                  senderName: 'RentMate',
                  senderType: 'ai',
                  messageType: 'internal',
                });
              }
              addAiMessage(event.reply!, { taskId });
              handleAgentActions(event.actions ?? [], taskId);
            } else if (event.type === 'error') {
              // Capture and throw after the loop so it reaches the outer catch
              sseError = new Error(event.message ?? 'AI unavailable');
            }
          }
          if (sseError) throw sseError;
        }

        // If the stream closed without a `done` event (e.g. proxy timeout while
        // run_and_persist was still running), reload messages from the DB so the
        // response that was written server-side becomes visible.
        if (!receivedDone) {
          setTimeout(() => {
            graphqlQuery<{ task: { messages: Parameters<typeof apiMessagesToChatThread>[0] } | null }>(
              TASK_QUERY, { uid: taskId }
            ).then(result => {
              if (result.task) setTaskMessages(taskId, apiMessagesToChatThread(result.task.messages ?? []));
            }).catch(() => {});
          }, 2000);
        }
      } else {
        // Session chat or suggestion discussion — same SSE stream as task chat
        const progressLines: string[] = [];
        const history = messages
          .filter(m => m.messageType === 'message' || !m.messageType)
          .map(m => ({ role: m.role === 'assistant' ? 'assistant' : 'user', content: m.content }));

        const suggestionHint = activeSuggestion
          ? `Discussing suggestion: "${activeSuggestion.title}". ${activeSuggestion.body ?? ''}`
          : '';
        const contextPrefix = suggestionHint || activeSession?.pageContext || chatPanel.pageContext || '';

        const res = await fetch('/chat', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({
            message: contextPrefix ? `[${contextPrefix}]\n\n${userMessage}` : userMessage,
            conversation_history: history,
          }),
        });
        if (!res.ok) {
          let detail = `Service unavailable (${res.status})`;
          try { const d = await res.json(); if (d.detail) detail = String(d.detail); } catch { /* ignore */ }
          throw new Error(detail);
        }

        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';
          let sseError: Error | null = null;
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            let event: { type: string; text?: string; reply?: string; message?: string };
            try { event = JSON.parse(line.slice(6)); } catch { continue; }
            if (event.type === 'progress') {
              progressLines.push(event.text as string);
              setProgressLog(prev => [...prev, event.text as string]);
            } else if (event.type === 'done') {
              if (progressLines.length > 0) {
                addChatMessage({ suggestionId, sessionId }, {
                  id: `thinking-${Date.now()}`,
                  role: 'assistant',
                  content: progressLines.join('\n'),
                  timestamp: new Date(),
                  senderName: 'RentMate',
                  senderType: 'ai',
                  messageType: 'internal',
                });
              }
              addAiMessage(event.reply!, { taskId, suggestionId, sessionId });
            } else if (event.type === 'error') {
              sseError = new Error(event.message ?? 'AI unavailable');
            }
          }
          if (sseError) throw sseError;
        }
      }
    } catch (e) {
      console.error('Chat error:', e);
      const errorMsg = e instanceof Error ? e.message : "I'm having trouble connecting right now.";
      addAiMessage(errorMsg, { taskId, suggestionId, sessionId });
      toast.error('RentMate is unavailable right now.');
    } finally {
      setIsTyping(false);
      setProgressLog([]);
    }
  };

  const handleFileUpload = async (file: File) => {
    const taskId = chatPanel.taskId;
    if (!taskId) return;

    const tempId = `uploading-${Date.now()}`;
    const tempDoc: ManagedDocument = {
      id: tempId,
      fileName: file.name,
      fileType: file.type,
      fileSize: file.size,
      documentType: 'other',
      status: 'uploading',
      uploadedAt: new Date(),
      tags: [],
      actionDeskTaskId: taskId,
    };
    addDocument(tempDoc);

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('document_type', 'other');
      formData.append('task_id', taskId);
      const res = await authFetch('/api/upload-document', { method: 'POST', body: formData });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { document_id } = await res.json();

      replaceDocument(tempId, { ...tempDoc, id: document_id, status: 'analyzing' });

      const msgContent = `Attached: ${file.name}`;
      const contextMsg: ChatMessage = {
        id: `msg-${Date.now()}`,
        role: 'user',
        content: msgContent,
        timestamp: new Date(),
        senderName: 'You',
        senderType: 'manager',
        messageType: 'context',
      };
      addChatMessage({ taskId }, contextMsg);
      if (activeTask?.aiConversationId) {
        await graphqlQuery(SEND_MESSAGE_MUTATION, {
          input: { conversationId: activeTask.aiConversationId, body: msgContent, messageType: 'context', senderName: 'You', isAi: false },
        });
      }
    } catch {
      removeDocument(tempId);
      toast.error('Failed to upload file. Please try again.');
    }
  };

  const handleSend = (content: string, insertedFromMessageId?: string) => {
    const userMsg: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: 'user',
      content,
      timestamp: new Date(),
      senderName: 'You',
      senderType: 'manager',
      messageType: 'message',
    };
    addChatMessage(
      { taskId: chatPanel.taskId, suggestionId: chatPanel.suggestionId, sessionId: chatPanel.sessionId },
      userMsg
    );

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

    callAI(content);
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
      : (activeSession?.title ?? 'Ask RentMate');

  const placeholder = activeTask
    ? 'Reply in this thread...'
    : activeSuggestion
      ? 'Discuss this suggestion...'
      : 'Ask RentMate anything...';

  return (
    <div className="fixed inset-0 z-50 bg-card flex flex-col overflow-hidden md:static md:inset-auto md:z-auto md:w-[320px] lg:w-[380px] md:border-l md:shrink-0 md:h-full">
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
          <Button variant="ghost" size="icon" onClick={closeChat} className="h-7 w-7 rounded-lg shrink-0 hover:bg-muted hover:text-muted-foreground">
            <X className="h-4 w-4" />
          </Button>
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
                  onClick={() => {
                    updateTask(chatPanel.taskId!, { mode: 'autonomous' });
                    addChatMessage(
                      { taskId: chatPanel.taskId },
                      { id: `msg-${Date.now()}`, role: 'assistant', content: 'RentMate turned back on for this task. I\'ll handle things autonomously.', timestamp: new Date(), senderName: 'RentMate', messageType: 'internal' }
                    );
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
          onValueChange={v => setActiveTaskTab(v as 'chat' | 'ai')}
          className="flex-1 flex flex-col min-h-0"
        >
          <TabsList className="shrink-0 mx-3 mt-2 mb-0 h-8 self-start gap-1 bg-muted/50">
            <TabsTrigger value="chat" className="text-xs h-6 px-3">Chat</TabsTrigger>
            <TabsTrigger value="ai" className="text-xs h-6 px-3">AI</TabsTrigger>
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
              </div>
            </ScrollArea>
            {(proposals.length > 0 || actionProposals.length > 0) && (
              <div className="p-3 border-t space-y-2 shrink-0">
                {actionProposals.map(p => (
                  <AgentActionConfirm
                    key={p._proposalId}
                    proposal={p}
                    onDismiss={(id) => setActionProposals(prev => prev.filter(x => x._proposalId !== id))}
                  />
                ))}
                {proposals.map(p => (
                  <AgentTaskProposal
                    key={p._proposalId}
                    proposal={p}
                    onDismiss={(id) => setProposals(prev => prev.filter(x => x._proposalId !== id))}
                  />
                ))}
              </div>
            )}
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
                <ChatInput ref={chatInputRef} onSend={handleSend} onInsertCleared={handleInsertCleared} placeholder={placeholder} disabled={isTyping} onFileUpload={handleFileUpload} />
              </div>
            )}
          </TabsContent>

          {/* Chat tab — participant conversation */}
          <TabsContent value="chat" className="hidden data-[state=active]:flex flex-1 flex-col min-h-0 mt-0">
            {/* Participant chips */}
            <div className="flex items-center gap-1.5 px-3 py-1.5 border-b bg-muted/20 shrink-0 flex-wrap">
              {activeTask.participants.filter(p => p.type === 'tenant' || p.type === 'vendor').length === 0 ? (
                <span className="text-[11px] text-muted-foreground italic">No external participants yet</span>
              ) : (
                activeTask.participants
                  .filter(p => p.type === 'tenant' || p.type === 'vendor')
                  .map((p, idx) => (
                    <Badge key={p.id ?? `${p.name}-${idx}`} variant="secondary" className="text-[10px] rounded-lg gap-1">
                      <span className="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-muted-foreground/20 text-[9px] font-bold">
                        {p.name.charAt(0).toUpperCase()}
                      </span>
                      {p.name}
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
                {!participantLoading && !activeTask.parentConversationId && (
                  <div className="text-center py-8 text-muted-foreground">
                    <Users className="h-8 w-8 mx-auto mb-2 opacity-40" />
                    <p className="text-sm font-medium">No participant conversation</p>
                    <p className="text-xs mt-1">This task has no linked conversation with a tenant or vendor.</p>
                  </div>
                )}
                {!participantLoading && activeTask.parentConversationId && participantMessages.length === 0 && (
                  <div className="text-center py-8 text-muted-foreground">
                    <MessageSquare className="h-8 w-8 mx-auto mb-2 opacity-40" />
                    <p className="text-sm font-medium">No messages yet</p>
                  </div>
                )}
                {participantMessages.map(msg => (
                  <ChatMessageBubble key={msg.id} message={msg} />
                ))}
              </div>
            </ScrollArea>
            {activeTask.parentConversationId ? (
              isAutonomous ? (
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
                          input: { conversationId: activeTask!.externalConversationId!, body: content },
                        });
                      } catch {
                        toast.error('Failed to send message');
                      }
                    }}
                    placeholder="Reply to participant…"
                  />
                </div>
              )
            ) : null}
          </TabsContent>
        </Tabs>
      ) : (
        <>
          {/* Messages (non-task) */}
          <ScrollArea className="flex-1 overflow-x-hidden" ref={scrollRef}>
            <div className="p-4 space-y-4 w-full overflow-x-hidden">
              {messages.length === 0 && !isTyping && (
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
                    onApprovalAction={activeSuggestion ? async (_messageId, action, editedBody) => {
                      const { graphqlQuery: gql, ACT_ON_SUGGESTION_MUTATION } = await import('@/data/api');
                      const result = await gql<{ actOnSuggestion: { uid: string; status: string } }>(
                        ACT_ON_SUGGESTION_MUTATION,
                        { uid: activeSuggestion.id, action, editedBody: editedBody ?? null },
                      );
                      updateSuggestionStatus(activeSuggestion.id, result.actOnSuggestion.status as 'accepted' | 'dismissed');
                      closeChat();
                    } : undefined}
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
            </div>
          </ScrollArea>
          {(proposals.length > 0 || actionProposals.length > 0) && (
            <div className="p-3 border-t space-y-2 shrink-0">
              {actionProposals.map(p => (
                <AgentActionConfirm
                  key={p._proposalId}
                  proposal={p}
                  onDismiss={(id) => setActionProposals(prev => prev.filter(x => x._proposalId !== id))}
                />
              ))}
              {proposals.map(p => (
                <AgentTaskProposal
                  key={p._proposalId}
                  proposal={p}
                  onDismiss={(id) => setProposals(prev => prev.filter(x => x._proposalId !== id))}
                />
              ))}
            </div>
          )}
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
            <ChatInput ref={chatInputRef} onSend={handleSend} onInsertCleared={handleInsertCleared} placeholder={placeholder} disabled={isTyping} />
          )}
        </>
      )}
    </div>
  );
}
