import { useRef, useEffect, useMemo, useState } from 'react';
import { X, Bot, Sparkles, Users, Zap, ShieldCheck, Hand, Lock, MessageSquare, RotateCcw, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { ChatMessageBubble } from './ChatMessage';
import { ChatInput, ChatInputHandle } from './ChatInput';
import { useApp } from '@/context/AppContext';
import { ActionDeskTask, ChatMessage, categoryLabels, TaskMode } from '@/data/mockData';
import { getToken } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import { AgentTaskProposal, AgentProposedTask } from './AgentTaskProposal';
import { graphqlQuery, TASK_QUERY, ADD_TASK_MESSAGE_MUTATION } from '@/data/api';
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
  const { chatPanel, closeChat, suggestions, actionDeskTasks, addChatMessage, updateTaskMessage, setTaskMessages, updateTask, globalChatThread } = useApp();
  const [dismissing, setDismissing] = useState(false);

  const handleDismiss = async () => {
    if (!chatPanel.taskId) return;
    const taskId = chatPanel.taskId;
    setDismissing(true);
    try {
      await graphqlQuery<unknown>(`mutation { updateTaskStatus(uid: "${taskId}", status: "dismissed") { uid } }`);
      await graphqlQuery(ADD_TASK_MESSAGE_MUTATION, {
        input: { taskId, body: 'Task dismissed — this item will not be re-created by automations.', messageType: 'internal', senderName: 'RentMate', isAi: true },
      });
      addChatMessage({ taskId }, { id: `dismiss-${Date.now()}`, role: 'assistant', content: 'Task dismissed — this item will not be re-created by automations.', timestamp: new Date(), senderName: 'RentMate', messageType: 'internal' });
      updateTask(taskId, { status: 'cancelled' });
    } finally {
      setDismissing(false);
    }
  };
  const scrollRef = useRef<HTMLDivElement>(null);
  const chatInputRef = useRef<ChatInputHandle>(null);
  const [isTyping, setIsTyping] = useState(false);
  const [progressLog, setProgressLog] = useState<string[]>([]);
  const [proposals, setProposals] = useState<AgentProposedTask[]>([]);

  // Clear proposals when switching tasks so follow-ups from other tasks don't bleed through
  useEffect(() => {
    setProposals([]);
  }, [chatPanel.taskId]);

  const activeSuggestion = useMemo(() =>
    chatPanel.suggestionId ? suggestions.find(s => s.id === chatPanel.suggestionId) : null,
    [chatPanel.suggestionId, suggestions]
  );

  const activeTask = useMemo(() =>
    chatPanel.taskId ? actionDeskTasks.find(t => t.id === chatPanel.taskId) : null,
    [chatPanel.taskId, actionDeskTasks]
  );

  const messages = activeTask
    ? activeTask.chatThread
    : activeSuggestion
      ? activeSuggestion.chatThread
      : globalChatThread;

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

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isTyping]);

  // Refresh task messages from DB whenever a task is opened, so messages
  // persisted server-side (e.g. agent response after navigation) are visible.
  useEffect(() => {
    if (!chatPanel.taskId) return;
    const taskId = chatPanel.taskId;
    graphqlQuery<{ task: { messages: Parameters<typeof apiMessagesToChatThread>[0] } | null }>(
      TASK_QUERY, { uid: taskId }
    ).then(result => {
      if (result.task) {
        setTaskMessages(taskId, apiMessagesToChatThread(result.task.messages ?? []));
      }
    }).catch(() => {
      // silently ignore — stale local state is still better than crashing
    });
  }, [chatPanel.taskId]); // eslint-disable-line react-hooks/exhaustive-deps

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

  const addAiMessage = (content: string, context: { taskId?: string | null; suggestionId?: string | null }) => {
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
      } else if (act.action === 'task_closed' && taskId) {
        updateTask(taskId, { status: 'resolved' });
      } else if (act.action === 'mode_changed' && taskId) {
        updateTask(taskId, { mode: act.mode as ActionDeskTask['mode'] });
      }
    }
  };

  const callAI = async (userMessage: string) => {
    setIsTyping(true);
    setProgressLog([]);
    try {
      if (chatPanel.taskId) {
        // Task thread — SSE stream with live progress
        const res = await fetch('/chat/task', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ task_id: chatPanel.taskId, message: userMessage }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const event = JSON.parse(line.slice(6));
              if (event.type === 'progress') {
                setProgressLog(prev => [...prev, event.text as string]);
              } else if (event.type === 'done') {
                addAiMessage(event.reply, { taskId: chatPanel.taskId });
                handleAgentActions(event.actions ?? [], chatPanel.taskId);
              } else if (event.type === 'error') {
                throw new Error(event.message);
              }
            } catch (parseErr) {
              // ignore malformed lines
            }
          }
        }
      } else {
        // Global chat or suggestion discussion
        const history = messages
          .filter(m => m.messageType === 'message' || !m.messageType)
          .map(m => ({ role: m.role === 'assistant' ? 'assistant' : 'user', content: m.content }));

        // Build context hint for suggestion discussions
        const suggestionHint = activeSuggestion
          ? `Discussing suggestion: "${activeSuggestion.title}". ${activeSuggestion.recommendedAction ?? ''}`
          : '';

        const res = await fetch('/chat', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({
            message: suggestionHint ? `[${suggestionHint}]\n\n${userMessage}` : userMessage,
            conversation_history: history,
          }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        addAiMessage(data.reply, { taskId: chatPanel.taskId, suggestionId: chatPanel.suggestionId });
      }
    } catch (e) {
      console.error('Chat error:', e);
      toast.error('RentMate is unavailable right now. Please try again.');
    } finally {
      setIsTyping(false);
      setProgressLog([]);
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
      { taskId: chatPanel.taskId, suggestionId: chatPanel.suggestionId },
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
      : 'Chat with RentMate';

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
        <Button variant="ghost" size="icon" onClick={closeChat} className="h-7 w-7 rounded-lg shrink-0 hover:bg-muted hover:text-muted-foreground">
          <X className="h-4 w-4" />
        </Button>
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
              <Button size="sm" variant="ghost" className="h-6 rounded-lg text-[11px] px-2 text-muted-foreground/60 hover:text-muted-foreground" disabled={dismissing} onClick={handleDismiss}>
                {dismissing ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Dismiss'}
              </Button>
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
          <p className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">{activeSuggestion.recommendedAction}</p>
        </div>
      )}

      {/* Messages */}
      <ScrollArea className="flex-1 overflow-x-hidden" ref={scrollRef}>
        <div className="p-4 space-y-4 w-full overflow-x-hidden">
          {messages.length === 0 && !isTyping && (
            <div className="text-center py-8 text-muted-foreground">
              <Bot className="h-8 w-8 mx-auto mb-2 opacity-40" />
              <p className="text-sm font-medium">
                {activeSuggestion
                  ? 'Discuss this suggestion with AI'
                  : activeTask
                    ? 'Chat with RentMate about this task'
                    : 'Ask me anything about your properties'
                }
              </p>
              <p className="text-xs mt-1">
                {activeSuggestion
                  ? 'Try: "Can we lower this?" or "Make it more friendly"'
                  : activeTask
                    ? 'Try: "Draft a message to the tenant" or "What should I do next?"'
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
                onApprove={(messageId) => {
                  const found = messages.find(m => m.id === messageId);
                  if (found?.draftReply) {
                    chatInputRef.current?.insertText(found.draftReply, messageId);
                  }
                }}
                onReject={(messageId) => {
                  if (chatPanel.taskId) {
                    updateTaskMessage(chatPanel.taskId, messageId, { approvalStatus: 'rejected' });
                    updateTask(chatPanel.taskId, { mode: 'manual' });
                    setTimeout(() => {
                      addChatMessage(
                        { taskId: chatPanel.taskId },
                        { id: `msg-${Date.now()}`, role: 'assistant', content: 'Rejected — switching to manual mode. You\'re in control now.', timestamp: new Date(), senderName: 'RentMate', messageType: 'internal' }
                      );
                    }, 300);
                  }
                }}
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
      {proposals.length > 0 && (
        <div className="p-3 border-t space-y-2 shrink-0">
          {proposals.map(p => (
            <AgentTaskProposal
              key={p._proposalId}
              proposal={p}
              onDismiss={(id) => setProposals(prev => prev.filter(x => x._proposalId !== id))}
            />
          ))}
        </div>
      )}

      {/* Input */}
      {activeTask && (activeTask.status === 'cancelled' || activeTask.status === 'resolved') ? (
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
              await graphqlQuery(ADD_TASK_MESSAGE_MUTATION, {
                input: { taskId, body: 'Task re-opened.', messageType: 'internal', senderName: 'RentMate', isAi: true },
              });
              updateTask(taskId, { status: 'active' });
              addChatMessage({ taskId }, { id: `msg-${Date.now()}`, role: 'assistant', content: 'Task re-opened.', timestamp: new Date(), senderName: 'RentMate', messageType: 'internal' });
            }}
          >
            <RotateCcw className="h-3 w-3" />
            Re-open
          </Button>
        </div>
      ) : isAutonomous && chatPanel.taskId ? (
        <div className="flex items-center gap-2 px-4 py-3 border-t bg-muted/30 shrink-0">
          <Zap className="h-4 w-4 text-accent shrink-0" />
          <p className="flex-1 text-xs text-muted-foreground">RentMate is handling this conversation autonomously.</p>
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
      ) : chatPanel.taskId ? (
        <div className="border-t shrink-0">
          <ChatInput ref={chatInputRef} onSend={handleSend} onInsertCleared={handleInsertCleared} placeholder={placeholder} disabled={isTyping} />
        </div>
      ) : (
        <ChatInput ref={chatInputRef} onSend={handleSend} onInsertCleared={handleInsertCleared} placeholder={placeholder} disabled={isTyping} />
      )}
    </div>
  );
}
