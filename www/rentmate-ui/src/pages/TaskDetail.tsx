import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Bot, Eye, Loader2, MessageCircle, Target } from 'lucide-react';
import { formatDistanceToNow, parseISO } from 'date-fns';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useApp } from '@/context/AppContext';
import { ActionDeskTask, LinkedConversation, categoryLabels } from '@/data/mockData';
import { toast } from 'sonner';
import { ChatPanel, performTaskDismiss, type EmbeddedTaskThreadSelection } from '@/components/chat/ChatPanel';
import { ConvRow, ConvSummary } from '@/components/chat/ConvRow';
import { ProgressSteps } from '@/components/chat/ProgressSteps';
import { markTaskSeen, sendMessage, updateTask as updateTaskMutation, updateTaskGoal, updateTaskStatus } from '@/graphql/client';
import { CONVERSATION_READ_EVENT, getReadConversationId } from '@/lib/conversationReadEvents';

const CATEGORY_OPTIONS = [
  'rent',
  'maintenance',
  'leasing',
  'compliance',
  'other',
] as const;

const URGENCY_OPTIONS = ['low', 'medium', 'high', 'critical'] as const;
const STATUS_OPTIONS = ['active', 'paused', 'resolved', 'cancelled'] as const;
type TaskDetailPane = 'list' | 'chat' | 'task';

function sentenceCase(value: string): string {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function isAiConversationType(conversationType: string | null | undefined): boolean {
  return conversationType === 'task_ai' || conversationType === 'suggestion_ai' || conversationType === 'user_ai';
}

function linkedToConvSummary(lc: LinkedConversation): ConvSummary {
  const isAi = isAiConversationType(lc.conversationType);
  const contact = lc.participants?.[0];
  return {
    uid: lc.uid,
    // ConvRow / TAB_CONFIG key the type color + icon on these values.
    conversationType: isAi ? 'user_ai' : lc.conversationType,
    // AI row shows the default "RentMate" treatment; external rows show the
    // contact's name so multiple vendors on one task stay distinguishable.
    title: isAi ? null : contact?.name ?? lc.label,
    lastMessageAt: lc.lastMessageAt ?? null,
    updatedAt: lc.lastMessageAt ?? '',
    lastMessageBody: null,
    lastMessageSenderName: null,
    propertyName: null,
    participantCount: lc.participants?.length ?? 0,
    unreadCount: lc.unreadCount ?? 0,
  };
}

function getDefaultTaskConversationId(task: ActionDeskTask): string | null {
  if (task.aiConversationId) return task.aiConversationId;
  const linkedAiConversation = (task.linkedConversations ?? []).find(
    lc => isAiConversationType(lc.conversationType),
  );
  return linkedAiConversation?.uid ?? null;
}

function TaskConversationList({
  task,
  selectedThread,
  onSelectAi,
  onSelectConversation,
}: {
  task: ActionDeskTask;
  selectedThread: EmbeddedTaskThreadSelection;
  onSelectAi: () => void;
  onSelectConversation: (id: string) => void;
}) {
  const conversations = useMemo<LinkedConversation[]>(() => {
    const linked = task.linkedConversations ?? [];
    const externals = linked
      .filter(lc => !isAiConversationType(lc.conversationType))
      .slice()
      .sort((a, b) => {
        const at = a.lastMessageAt ? new Date(a.lastMessageAt).getTime() : 0;
        const bt = b.lastMessageAt ? new Date(b.lastMessageAt).getTime() : 0;
        return bt - at;
      });
    return externals;
  }, [task.linkedConversations]);

  const aiConversationId = getDefaultTaskConversationId(task);
  const aiSummary = linkedToConvSummary({
    uid: aiConversationId ?? '__task-ai__',
    label: 'RentMate',
    conversationType: 'task_ai',
    messageCount: 0,
    participants: [],
  });

  if (conversations.length === 0) {
    return (
      <ScrollArea className="h-full">
        <div className="p-2 space-y-2">
          <ConvRow
            conv={aiSummary}
            isActive={selectedThread.kind === 'ai'}
            onClick={onSelectAi}
          />
        </div>
      </ScrollArea>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-2 space-y-2">
        <ConvRow
          key={aiSummary.uid}
          conv={aiSummary}
          isActive={selectedThread.kind === 'ai'}
          onClick={onSelectAi}
        />
        {conversations.map(c => (
          <ConvRow
            key={c.uid}
            conv={linkedToConvSummary(c)}
            isActive={selectedThread.kind === 'conversation' && c.uid === selectedThread.id}
            onClick={() => onSelectConversation(c.uid)}
          />
        ))}
      </div>
    </ScrollArea>
  );
}

function GoalEditor({
  task,
  onSave,
}: {
  task: ActionDeskTask;
  onSave: (next: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(task.goal ?? '');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setValue(task.goal ?? '');
  }, [task.goal, task.id]);

  const commit = useCallback(async () => {
    const next = value.trim();
    if (next === (task.goal ?? '').trim()) {
      setEditing(false);
      return;
    }
    if (!next) {
      toast.error('Goal is required');
      return;
    }
    setSaving(true);
    try {
      await onSave(next);
      setEditing(false);
    } catch (err) {
      toast.error('Could not save the goal');
    } finally {
      setSaving(false);
    }
  }, [value, task.goal, onSave]);

  const cancel = useCallback(() => {
    setValue(task.goal ?? '');
    setEditing(false);
  }, [task.goal]);

  if (editing) {
    return (
      <div className="space-y-2">
        <Textarea
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => {
            if ((e.key === 'Enter' && (e.metaKey || e.ctrlKey)) || (e.key === 'Enter' && !e.shiftKey)) {
              e.preventDefault();
              void commit();
            } else if (e.key === 'Escape') {
              e.preventDefault();
              cancel();
            }
          }}
          autoFocus
          rows={4}
          placeholder="What does 'done' look like?"
          className="text-sm"
        />
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] text-muted-foreground">Enter to save · Shift+Enter for newline · Esc to cancel</span>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={cancel} disabled={saving}>Cancel</Button>
            <Button size="sm" onClick={commit} disabled={saving}>
              {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Save'}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  if (!task.goal) {
    return (
      <button
        type="button"
        onClick={() => setEditing(true)}
        className="w-full text-left px-3 py-3 rounded-lg border border-dashed border-muted-foreground/30 text-xs text-muted-foreground hover:bg-muted/40 transition-colors"
      >
        Add a goal — what does "done" look like?
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={() => setEditing(true)}
      className="w-full text-left px-3 py-3 rounded-lg bg-muted/40 hover:bg-muted/60 transition-colors"
    >
      <p className="text-sm whitespace-pre-wrap">{task.goal}</p>
      <span className="text-[10px] text-muted-foreground mt-1 block">Click to edit</span>
    </button>
  );
}

const REVIEW_STATUS_TONE: Record<string, { label: string; variant: 'default' | 'secondary' | 'outline' | 'destructive' }> = {
  on_track:     { label: 'On track',     variant: 'secondary' },
  needs_action: { label: 'Needs action', variant: 'destructive' },
  blocked:      { label: 'Blocked',      variant: 'destructive' },
  waiting:      { label: 'Waiting',      variant: 'outline' },
};

function AgentSection({ task, onSelectAi }: { task: ActionDeskTask; onSelectAi: () => void }) {
  const { triggerReviewStream } = useApp();

  const status = (task.lastReviewStatus ?? '').toLowerCase();
  const tone = status
    ? REVIEW_STATUS_TONE[status] ?? {
        label: status.replace(/_/g, ' ') || 'Unknown',
        variant: 'outline' as const,
      }
    : null;

  let reviewedLabel: string | null = null;
  if (task.lastReviewedAt) {
    try {
      reviewedLabel = formatDistanceToNow(parseISO(task.lastReviewedAt), { addSuffix: true });
    } catch {
      reviewedLabel = task.lastReviewedAt;
    }
  }

  const handleTrigger = useCallback(() => {
    // Switch the middle column to the AI thread so the manager sees the
    // agent's reasoning inline, then hand the SSE stream off to ChatPanel
    // (it drives the isTyping / progressLog UI the regular chat already has).
    onSelectAi();
    triggerReviewStream(task.id);
  }, [task.id, onSelectAi, triggerReviewStream]);

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-muted-foreground">
          <Eye className="h-3.5 w-3.5" />
          <h3 className="text-[11px] font-semibold uppercase tracking-wide">
            {reviewedLabel ? `Agent · ${reviewedLabel}` : 'Agent'}
          </h3>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-6 px-2 text-[10px]"
          onClick={handleTrigger}
        >
          Trigger Agent
        </Button>
      </div>
      {tone && (
        <Badge variant={tone.variant} className="text-[10px] capitalize">
          {tone.label}
        </Badge>
      )}
      {task.lastReviewSummary ? (
        <p className="text-xs text-foreground/90 whitespace-pre-wrap">
          {task.lastReviewSummary}
        </p>
      ) : (
        <p className="text-[11px] text-muted-foreground">
          No review yet. Click Trigger Agent to run one now.
        </p>
      )}
      {task.lastReviewNextStep && (
        <p className="text-[11px] text-muted-foreground whitespace-pre-wrap">
          <span className="font-semibold text-foreground/80">Next: </span>
          {task.lastReviewNextStep}
        </p>
      )}
    </section>
  );
}

function TaskGoalPanel({ task, onSelectAi }: { task: ActionDeskTask; onSelectAi: () => void }) {
  const { updateTask, addChatMessage, removeTask, closeChat, refreshData } = useApp();
  const navigate = useNavigate();
  const [dismissConfirm, setDismissConfirm] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [categoryValue, setCategoryValue] = useState(task.category ?? 'other');
  const [urgencyValue, setUrgencyValue] = useState(task.urgency ?? 'medium');
  const [statusValue, setStatusValue] = useState(task.status ?? 'active');

  // Reset confirm state when the task changes.
  useEffect(() => {
    setDismissConfirm(false);
  }, [task.id]);

  useEffect(() => {
    setCategoryValue(task.category ?? 'other');
    setUrgencyValue(task.urgency ?? 'medium');
    setStatusValue(task.status ?? 'active');
  }, [task.id, task.category, task.urgency, task.status]);

  const handleSave = useCallback(
    async (next: string) => {
      const cleaned = next.trim();
      if (!cleaned) {
        throw new Error('Goal is required');
      }
      updateTask(task.id, { goal: cleaned });
      try {
        await updateTaskGoal(task.id, cleaned);
        toast.success('Goal saved');
      } catch (err) {
        // revert on failure
        updateTask(task.id, { goal: task.goal ?? '' });
        throw err;
      }
    },
    [task.id, task.goal, updateTask],
  );

  const handleDismiss = useCallback(async () => {
    if (!dismissConfirm) {
      setDismissConfirm(true);
      return;
    }
    setDismissing(true);
    try {
      await performTaskDismiss({
        taskId: task.id,
        aiConversationId: task.aiConversationId,
        updateTaskStatusFn: updateTaskStatus,
        sendMessageFn: sendMessage,
        addChatMessage,
        updateTask,
        removeTask,
        closeChat,
        refreshData,
      });
      toast.info('Task dismissed');
      navigate('/tasks');
    } catch {
      toast.error('Failed to dismiss task');
    } finally {
      setDismissing(false);
      setDismissConfirm(false);
    }
  }, [dismissConfirm, task.id, task.aiConversationId, addChatMessage, updateTask, removeTask, closeChat, refreshData, navigate]);

  const handleDetailChange = useCallback(
    async (
      field: 'category' | 'urgency' | 'status',
      nextValue: string,
    ) => {
      const previousValue =
        field === 'category' ? categoryValue :
        field === 'urgency' ? urgencyValue :
        statusValue;
      if (nextValue === previousValue) return;

      if (field === 'category') setCategoryValue(nextValue);
      if (field === 'urgency') setUrgencyValue(nextValue);
      if (field === 'status') setStatusValue(nextValue);

      try {
        await updateTaskMutation({
          uid: task.id,
          category: field === 'category' ? nextValue : undefined,
          urgency: field === 'urgency' ? nextValue : undefined,
          taskStatus: field === 'status' ? nextValue : undefined,
        });
        await refreshData();
      } catch {
        if (field === 'category') setCategoryValue(previousValue);
        if (field === 'urgency') setUrgencyValue(previousValue);
        if (field === 'status') setStatusValue(previousValue);
        toast.error(`Failed to update task ${field}`);
      }
    },
    [categoryValue, urgencyValue, statusValue, task.id, refreshData],
  );

  return (
    <ScrollArea className="h-full">
      <div className="p-4 space-y-5">
        <section>
          <div className="flex items-center gap-1.5 mb-2 text-muted-foreground">
            <Target className="h-3.5 w-3.5" />
            <h3 className="text-[11px] font-semibold uppercase tracking-wide">Goal</h3>
          </div>
          <GoalEditor task={task} onSave={handleSave} />
        </section>

        <section>
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            Progress
          </h3>
          {task.steps && task.steps.length > 0 ? (
            <ProgressSteps steps={task.steps} />
          ) : (
            <p className="text-xs text-muted-foreground px-3">
              No progress steps recorded yet.
            </p>
          )}
        </section>

        <AgentSection task={task} onSelectAi={onSelectAi} />

        <section>
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            Details
          </h3>
          <div className="space-y-1.5 text-xs">
            <div className="flex items-center justify-between gap-3">
              <span className="text-muted-foreground">Category</span>
              <Select value={categoryValue} onValueChange={value => void handleDetailChange('category', value)}>
                <SelectTrigger className="h-7 w-[156px] text-[11px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CATEGORY_OPTIONS.map(option => (
                    <SelectItem key={option} value={option} className="text-[11px]">
                      {categoryLabels[option] ?? sentenceCase(option)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-muted-foreground">Urgency</span>
              <Select value={urgencyValue} onValueChange={value => void handleDetailChange('urgency', value)}>
                <SelectTrigger className="h-7 w-[156px] text-[11px] capitalize">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {URGENCY_OPTIONS.map(option => (
                    <SelectItem key={option} value={option} className="text-[11px] capitalize">
                      {option}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-muted-foreground">Status</span>
              <Select value={statusValue} onValueChange={value => void handleDetailChange('status', value)}>
                <SelectTrigger className="h-7 w-[156px] text-[11px] capitalize">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STATUS_OPTIONS.map(option => (
                    <SelectItem key={option} value={option} className="text-[11px] capitalize">
                      {option.replace(/_/g, ' ')}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </section>

        {statusValue === 'active' && (
          <section className="pt-2 border-t">
            <Button
              variant="ghost"
              size="sm"
              className={cn(
                'w-full justify-center gap-1.5 h-8 text-xs',
                dismissConfirm
                  ? 'text-destructive hover:text-destructive hover:bg-destructive/10'
                  : 'text-destructive/60 hover:text-destructive hover:bg-destructive/10',
              )}
              disabled={dismissing}
              onClick={handleDismiss}
              onBlur={() => setDismissConfirm(false)}
            >
              {dismissing ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : dismissConfirm ? (
                'Confirm dismiss'
              ) : (
                'Dismiss task'
              )}
            </Button>
          </section>
        )}
      </div>
    </ScrollArea>
  );
}

export default function TaskDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { actionDeskTasks, suggestions, openChat, closeChat, isLoading, updateTask } = useApp();
  const openedRef = useRef<string | null>(null);
  const seenTaskRef = useRef<string | null>(null);
  const suggestionId = searchParams.get('suggestion');
  const conversationId = searchParams.get('conversation');
  const messageId = searchParams.get('message');

  const task = useMemo(
    () => actionDeskTasks.find(t => String(t.id) === String(id)),
    [actionDeskTasks, id],
  );
  const [selectedThread, setSelectedThread] = useState<EmbeddedTaskThreadSelection>({ kind: 'ai' });
  const [mobilePane, setMobilePane] = useState<TaskDetailPane>('chat');
  const activeSuggestion = useMemo(
    () => suggestionId ? suggestions.find(s => s.id === suggestionId) : null,
    [suggestionId, suggestions],
  );

  // Open the task in ChatPanel state once — ChatPanel drives the middle column.
  // Close it on unmount so the slide-out doesn't pop back into other routes.
  useEffect(() => {
    if (!task) return;
    const openKey = `${task.id}:${suggestionId ?? ''}:${conversationId ?? ''}:${messageId ?? ''}`;
    if (openedRef.current === openKey) return;
    openedRef.current = openKey;
    openChat({ taskId: task.id, suggestionId, conversationId });
    return () => {
      openedRef.current = null;
      closeChat();
    };
  }, [task?.id, suggestionId, conversationId, messageId, openChat, closeChat]);

  // Reset the opened guard when the task id changes so navigation between
  // tasks still triggers openChat.
  useEffect(() => {
    openedRef.current = null;
  }, [id]);

  useEffect(() => {
    if (
      conversationId
      && (task?.linkedConversations ?? []).some(conversation => conversation.uid === conversationId)
    ) {
      setSelectedThread({ kind: 'conversation', id: conversationId, messageId });
      return;
    }
    const targetConversationId = activeSuggestion?.targetConversationId;
    if (
      targetConversationId
      && (task?.linkedConversations ?? []).some(conversation => conversation.uid === targetConversationId)
    ) {
      setSelectedThread({ kind: 'conversation', id: targetConversationId });
      return;
    }
    setSelectedThread({ kind: 'ai' });
  }, [activeSuggestion?.targetConversationId, conversationId, messageId, task?.id, task?.linkedConversations]);

  useEffect(() => {
    if (!task) return;
    if (seenTaskRef.current === task.id) return;
    seenTaskRef.current = task.id;
    if ((task.unreadCount ?? 0) > 0) {
      updateTask(task.id, { unreadCount: 0 });
    }
    void markTaskSeen(task.id).catch(() => {
      // Keep the local badge cleared for this session; next refresh will
      // rehydrate the server value if the mutation truly failed.
    });
  }, [task?.id, task?.unreadCount, updateTask]);

  useEffect(() => {
    if (!task) return;
    const onConversationRead = (event: Event) => {
      const conversationId = getReadConversationId(event);
      if (!conversationId) return;
      const linkedConversations = task.linkedConversations ?? [];
      if (!linkedConversations.some(lc => lc.uid === conversationId && (lc.unreadCount ?? 0) > 0)) return;
      updateTask(task.id, {
        linkedConversations: linkedConversations.map(lc => (
          lc.uid === conversationId ? { ...lc, unreadCount: 0 } : lc
        )),
      });
    };
    window.addEventListener(CONVERSATION_READ_EVENT, onConversationRead);
    return () => window.removeEventListener(CONVERSATION_READ_EVENT, onConversationRead);
  }, [task, updateTask]);

  useEffect(() => {
    if (selectedThread.kind !== 'conversation') return;
    const exists = (task?.linkedConversations ?? []).some(
      lc => !isAiConversationType(lc.conversationType) && lc.uid === selectedThread.id,
    );
    if (!exists) setSelectedThread({ kind: 'ai' });
  }, [task?.linkedConversations, selectedThread]);

  if (isLoading && !task) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!task) {
    return (
      <div className="p-6 space-y-3">
        <Button variant="ghost" size="sm" onClick={() => navigate(-1)} className="gap-1">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back
        </Button>
        <p className="text-sm text-muted-foreground">Task not found.</p>
      </div>
    );
  }

  const selectAiThread = () => {
    setSelectedThread({ kind: 'ai' });
    setMobilePane('chat');
  };

  const selectConversationThread = (convId: string) => {
    setSelectedThread({ kind: 'conversation', id: convId });
    setMobilePane('chat');
  };

  const mobileTabs: Array<{
    key: TaskDetailPane;
    label: string;
    icon: typeof MessageCircle;
  }> = [
    { key: 'list', label: 'Chat List', icon: MessageCircle },
    { key: 'chat', label: 'Chat', icon: Bot },
    { key: 'task', label: 'Task', icon: Target },
  ];

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-2 px-4 py-2 border-b">
        <Button variant="ghost" size="sm" onClick={() => navigate(-1)} className="gap-1 h-7 px-2">
          <ArrowLeft className="h-3.5 w-3.5" />
          <span className="text-xs">Back</span>
        </Button>
        <span className="text-xs text-muted-foreground">·</span>
        <h1 className="text-sm font-medium truncate">{task.title}</h1>
        {task.taskNumber != null && (
          <Badge variant="outline" className="text-[10px] font-mono">Task #{task.taskNumber}</Badge>
        )}
      </div>
      <div className="md:hidden flex shrink-0 border-b bg-card/40 backdrop-blur-sm">
        {mobileTabs.map(tab => {
          const Icon = tab.icon;
          const active = mobilePane === tab.key;
          return (
            <button
              key={tab.key}
              type="button"
              onClick={() => setMobilePane(tab.key)}
              aria-pressed={active}
              data-testid={`task-detail-tab-${tab.key}`}
              className={cn(
                'flex-1 flex items-center justify-center gap-1.5 py-2 text-xs font-medium transition-colors',
                active
                  ? 'border-b-2 border-primary text-foreground'
                  : 'border-b-2 border-transparent text-muted-foreground hover:text-foreground',
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {tab.label}
            </button>
          );
        })}
      </div>
      <div className="flex flex-col flex-1 min-h-0 md:grid md:grid-cols-[280px_minmax(0,1fr)_360px]">
        <aside
          className={cn(
            'border-r min-h-0 overflow-hidden',
            mobilePane === 'list' ? 'block flex-1' : 'hidden',
            'md:block md:h-full',
          )}
        >
          <TaskConversationList
            task={task}
            selectedThread={selectedThread}
            onSelectAi={selectAiThread}
            onSelectConversation={selectConversationThread}
          />
        </aside>
        <main
          className={cn(
            'min-h-0 overflow-hidden',
            mobilePane === 'chat' ? 'block flex-1' : 'hidden',
            'md:block md:h-full',
          )}
        >
          <ChatPanel embedded embeddedTaskSelection={selectedThread} />
        </main>
        <aside
          className={cn(
            'border-l min-h-0 overflow-hidden',
            mobilePane === 'task' ? 'block flex-1' : 'hidden',
            'md:block md:h-full',
          )}
        >
          <TaskGoalPanel task={task} onSelectAi={selectAiThread} />
        </aside>
      </div>
    </div>
  );
}
