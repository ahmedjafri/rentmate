import { useParams, Link, useNavigate } from 'react-router-dom';
import { useState, useEffect } from 'react';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import {
  ArrowLeft, Building2, Home, User, Wrench, Bot, Zap, ShieldCheck, Hand, Lock,
  MessageCircle, Clock, CheckCircle2, XCircle, PauseCircle, Calendar,
  AlertTriangle, ChevronRight, Eye, Brain, Target, ListChecks,
} from 'lucide-react';
import { ProgressSteps, TaskStep } from '@/components/chat/ProgressSteps';
import { ChatMessageBubble, formatMessageTime } from '@/components/chat/ChatMessage';
import { PageLoader } from '@/components/ui/page-loader';
import {
  ActionDeskTask, ChatMessage, TaskMode, SuggestionCategory,
  categoryColors, categoryLabels,
} from '@/data/mockData';
import { cn } from '@/lib/utils';
import { graphqlQuery, TASK_QUERY, CONVERSATION_MESSAGES_QUERY } from '@/data/api';
import { apiMessagesToChatThread } from '@/hooks/useApiData';
import { formatDistanceToNow } from 'date-fns';

// ---- Mode configuration ----

const modeConfig: Record<TaskMode, { label: string; icon: React.ElementType; className: string }> = {
  autonomous: { label: 'Autonomous', icon: Zap, className: 'bg-accent/15 text-accent' },
  waiting_approval: { label: 'Needs Approval', icon: ShieldCheck, className: 'bg-warning/15 text-warning-foreground' },
  manual: { label: 'Manual', icon: Hand, className: 'bg-muted text-muted-foreground' },
};

const statusConfig: Record<string, { label: string; icon: React.ElementType; className: string }> = {
  active: { label: 'Active', icon: Zap, className: 'text-primary' },
  paused: { label: 'Paused', icon: PauseCircle, className: 'text-muted-foreground' },
  resolved: { label: 'Resolved', icon: CheckCircle2, className: 'text-accent' },
  cancelled: { label: 'Cancelled', icon: XCircle, className: 'text-destructive' },
};

const urgencyConfig: Record<string, { label: string; className: string }> = {
  low: { label: 'Low', className: 'bg-muted text-muted-foreground' },
  medium: { label: 'Medium', className: 'bg-primary/10 text-primary' },
  high: { label: 'High', className: 'bg-warning/15 text-warning-foreground' },
  critical: { label: 'Critical', className: 'bg-destructive/10 text-destructive' },
};

// ---- Reasoning extraction ----

interface ReasoningEntry {
  type: 'goal' | 'plan' | 'action' | 'observation' | 'decision' | 'thinking';
  content: string;
  timestamp: Date;
}

function extractReasoning(messages: ChatMessage[]): ReasoningEntry[] {
  const entries: ReasoningEntry[] = [];

  for (const msg of messages) {
    if (!msg.content) continue;

    // AI messages with thinking blocks
    if (msg.role === 'assistant' && msg.messageType !== 'context') {
      const thinkMatch = msg.content.match(/<think>([\s\S]*?)<\/think>/);
      if (thinkMatch) {
        entries.push({
          type: 'thinking',
          content: thinkMatch[1].trim(),
          timestamp: msg.timestamp,
        });
      }

      // Context messages are agent observations/goals
      if (msg.messageType === 'internal') {
        entries.push({
          type: 'observation',
          content: msg.content.replace(/<think>[\s\S]*?<\/think>/, '').trim(),
          timestamp: msg.timestamp,
        });
      } else if (msg.content.toLowerCase().includes('plan') || msg.content.toLowerCase().includes('steps')) {
        entries.push({
          type: 'plan',
          content: msg.content.replace(/<think>[\s\S]*?<\/think>/, '').trim(),
          timestamp: msg.timestamp,
        });
      } else {
        const cleanContent = msg.content.replace(/<think>[\s\S]*?<\/think>/, '').trim();
        if (cleanContent) {
          entries.push({
            type: 'action',
            content: cleanContent,
            timestamp: msg.timestamp,
          });
        }
      }
    }

    // Context messages as goals
    if (msg.messageType === 'context') {
      entries.push({
        type: 'goal',
        content: msg.content,
        timestamp: msg.timestamp,
      });
    }
  }

  return entries;
}

const reasoningIcons: Record<ReasoningEntry['type'], React.ElementType> = {
  goal: Target,
  plan: ListChecks,
  action: Zap,
  observation: Eye,
  decision: Brain,
  thinking: Eye,
};

const reasoningLabels: Record<ReasoningEntry['type'], string> = {
  goal: 'Goal',
  plan: 'Plan',
  action: 'Action',
  observation: 'Observation',
  decision: 'Decision',
  thinking: 'Thinking',
};

const reasoningColors: Record<ReasoningEntry['type'], string> = {
  goal: 'text-primary',
  plan: 'text-accent',
  action: 'text-foreground',
  observation: 'text-muted-foreground',
  decision: 'text-warning-foreground',
  thinking: 'text-muted-foreground',
};

// ---- Component ----

const TaskCommandCenter = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { actionDeskTasks, properties, tenants, vendors, openChat, isLoading } = useApp();
  const [externalMessages, setExternalMessages] = useState<ChatMessage[]>([]);
  const [externalLoading, setExternalLoading] = useState(false);

  const task = actionDeskTasks.find(t => t.id === id);

  // Fetch external conversation messages if task has one
  useEffect(() => {
    if (!task?.externalConversationId) return;
    setExternalLoading(true);
    graphqlQuery<{ conversationMessages: { uid: string; body?: string; messageType?: string; senderName?: string; senderType?: string; isAi: boolean; isSystem: boolean; draftReply?: string; suggestionId?: string; sentAt: string }[] }>(
      CONVERSATION_MESSAGES_QUERY,
      { uid: task.externalConversationId },
    )
      .then(data => {
        setExternalMessages(
          apiMessagesToChatThread(
            (data.conversationMessages || []).map(m => ({
              ...m,
              relatedTaskIds: undefined,
              approvalStatus: undefined,
            })),
          ),
        );
      })
      .catch(() => {})
      .finally(() => setExternalLoading(false));
  }, [task?.externalConversationId]);

  if (isLoading) return <PageLoader />;

  if (!task) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <Link to="/tasks" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground mb-6">
          <ArrowLeft className="h-4 w-4" /> Back to Tasks
        </Link>
        <div className="text-center py-20">
          <h2 className="text-lg font-medium text-muted-foreground">Task not found</h2>
          <p className="text-sm text-muted-foreground mt-1">This task may have been deleted or you don't have access.</p>
        </div>
      </div>
    );
  }

  const mode = modeConfig[task.mode] ?? modeConfig.manual;
  const ModeIcon = mode.icon;
  const status = statusConfig[task.status] ?? statusConfig.active;
  const StatusIcon = status.icon;
  const urgency = task.urgency ? (urgencyConfig[task.urgency] ?? urgencyConfig.low) : null;

  const property = task.propertyId ? properties.find(p => p.id === task.propertyId) : null;
  const propertyTenants = task.propertyId
    ? tenants.filter(t => t.propertyId === task.propertyId && t.isActive)
    : [];
  const assignedVendor = task.assignedVendorId
    ? vendors.find(v => v.id === task.assignedVendorId)
    : null;

  // Reasoning traces
  const reasoning = extractReasoning(task.chatThread);

  // Separate AI messages from human/external for the conversations panel
  const aiMessages = task.chatThread.filter(m =>
    m.messageType !== 'context' && (m.role === 'assistant' || m.senderName === 'You'),
  );

  const steps = task.steps as TaskStep[] | undefined;

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      {/* Navigation */}
      <Link to="/tasks" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Back to Tasks
      </Link>

      {/* ---- Header ---- */}
      <div className="space-y-3">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              {task.taskNumber != null && (
                <span className="text-sm font-mono text-muted-foreground">#{task.taskNumber}</span>
              )}
              <h1 className="text-2xl font-bold">{task.title}</h1>
            </div>
            <div className="flex items-center gap-2 text-sm text-muted-foreground flex-wrap">
              <span className="flex items-center gap-1">
                <Calendar className="h-3.5 w-3.5" />
                Created {formatDistanceToNow(new Date(task.lastMessageAt), { addSuffix: true })}
              </span>
              {task.source && (
                <>
                  <span className="text-muted-foreground/40">|</span>
                  <span>Source: {task.source}</span>
                </>
              )}
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => openChat({ taskId: task.id })}
          >
            <MessageCircle className="h-4 w-4 mr-1.5" />
            Open Chat
          </Button>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant="secondary" className={cn('text-xs rounded-lg gap-1', status.className, 'bg-opacity-15')}>
            <StatusIcon className="h-3 w-3" />
            {status.label}
          </Badge>
          <Badge variant="secondary" className={cn('text-xs rounded-lg gap-1', mode.className)}>
            <ModeIcon className="h-3 w-3" />
            {mode.label}
          </Badge>
          {task.category && (
            <Badge variant="secondary" className={cn('text-xs rounded-lg', categoryColors[task.category as SuggestionCategory])}>
              {categoryLabels[task.category as SuggestionCategory] ?? task.category}
            </Badge>
          )}
          {urgency && (
            <Badge variant="secondary" className={cn('text-xs rounded-lg', urgency.className)}>
              {urgency.label} urgency
            </Badge>
          )}
          {task.confidential && (
            <Badge variant="secondary" className="text-xs rounded-lg gap-1 bg-destructive/10 text-destructive">
              <Lock className="h-3 w-3" />
              Confidential
            </Badge>
          )}
        </div>
      </div>

      <Separator />

      {/* ---- Main Layout: 2-column ---- */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column: Plan + Reasoning (takes 2/3 width) */}
        <div className="lg:col-span-2 space-y-6">
          {/* Agent Plan & Progress */}
          <Card className="overflow-hidden">
            <div className="px-4 py-3 border-b flex items-center gap-2">
              <ListChecks className="h-4 w-4 text-primary" />
              <h2 className="font-semibold text-sm">Agent Plan & Progress</h2>
              {steps && steps.length > 0 && (
                <span className="text-xs text-muted-foreground ml-auto">
                  {steps.filter(s => s.status === 'done').length}/{steps.length} complete
                </span>
              )}
            </div>
            <ProgressSteps steps={steps} />
          </Card>

          {/* Reasoning Traces */}
          <Card className="overflow-hidden">
            <div className="px-4 py-3 border-b flex items-center gap-2">
              <Brain className="h-4 w-4 text-primary" />
              <h2 className="font-semibold text-sm">Agent Reasoning</h2>
            </div>
            {reasoning.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <Brain className="h-8 w-8 mb-2 opacity-40" />
                <p className="text-sm font-medium">No reasoning traces yet</p>
                <p className="text-xs mt-1 text-center px-6">
                  Reasoning will appear here as the agent processes this task.
                </p>
              </div>
            ) : (
              <ScrollArea className="max-h-96">
                <div className="px-4 py-3 space-y-0">
                  {reasoning.map((entry, i) => {
                    const Icon = reasoningIcons[entry.type];
                    const isLast = i === reasoning.length - 1;
                    return (
                      <div key={i} className="flex gap-3 relative">
                        {!isLast && (
                          <div className="absolute left-[11px] top-6 w-px bottom-0 bg-border" />
                        )}
                        <div className="shrink-0 mt-0.5 z-10">
                          <div className={cn(
                            'flex h-[22px] w-[22px] items-center justify-center rounded-full',
                            entry.type === 'goal' ? 'bg-primary/15' : 'bg-muted',
                          )}>
                            <Icon className={cn('h-3 w-3', reasoningColors[entry.type])} />
                          </div>
                        </div>
                        <div className={cn('pb-4 min-w-0', isLast && 'pb-1')}>
                          <div className="flex items-center gap-2">
                            <span className={cn('text-[11px] font-medium uppercase tracking-wide', reasoningColors[entry.type])}>
                              {reasoningLabels[entry.type]}
                            </span>
                            <span className="text-[10px] text-muted-foreground">
                              {formatMessageTime(entry.timestamp)}
                            </span>
                          </div>
                          <p className="text-sm text-foreground/80 leading-relaxed mt-0.5 whitespace-pre-wrap">
                            {entry.content.length > 300 ? entry.content.slice(0, 300) + '...' : entry.content}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </ScrollArea>
            )}
          </Card>

          {/* Conversations */}
          <Card className="overflow-hidden">
            <Tabs defaultValue="ai">
              <div className="px-4 py-3 border-b flex items-center gap-2">
                <MessageCircle className="h-4 w-4 text-primary" />
                <h2 className="font-semibold text-sm">Conversations</h2>
                <TabsList className="ml-auto h-7">
                  <TabsTrigger value="ai" className="text-xs h-6 px-2.5">
                    <Bot className="h-3 w-3 mr-1" />
                    AI Thread
                  </TabsTrigger>
                  {task.externalConversationId && (
                    <TabsTrigger value="external" className="text-xs h-6 px-2.5">
                      <User className="h-3 w-3 mr-1" />
                      External
                    </TabsTrigger>
                  )}
                </TabsList>
              </div>

              <TabsContent value="ai" className="m-0">
                <ScrollArea className="max-h-96">
                  <div className="p-4 space-y-3">
                    {aiMessages.length === 0 ? (
                      <p className="text-sm text-muted-foreground text-center py-8">No messages in AI thread yet.</p>
                    ) : (
                      aiMessages.map(msg => (
                        <ChatMessageBubble key={msg.id} message={msg} />
                      ))
                    )}
                  </div>
                </ScrollArea>
              </TabsContent>

              {task.externalConversationId && (
                <TabsContent value="external" className="m-0">
                  <ScrollArea className="max-h-96">
                    <div className="p-4 space-y-3">
                      {externalLoading ? (
                        <p className="text-sm text-muted-foreground text-center py-8">Loading messages...</p>
                      ) : externalMessages.length === 0 ? (
                        <p className="text-sm text-muted-foreground text-center py-8">No external messages yet.</p>
                      ) : (
                        externalMessages.map(msg => (
                          <ChatMessageBubble key={msg.id} message={msg} />
                        ))
                      )}
                    </div>
                  </ScrollArea>
                </TabsContent>
              )}
            </Tabs>
          </Card>
        </div>

        {/* Right column: Linked entities */}
        <div className="space-y-4">
          {/* Property */}
          {(property || task.propertyName) && (
            <Card className="p-4 space-y-2">
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
                <Building2 className="h-3.5 w-3.5" />
                Property
              </h3>
              {property ? (
                <Link
                  to={`/properties/${property.id}`}
                  className="flex items-center gap-3 rounded-lg p-2 -mx-2 hover:bg-muted transition-colors group"
                >
                  <div className="h-9 w-9 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                    <Building2 className="h-4 w-4 text-primary" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate group-hover:text-primary transition-colors">
                      {property.name || property.address}
                    </p>
                    {property.name && (
                      <p className="text-[11px] text-muted-foreground truncate">{property.address}</p>
                    )}
                  </div>
                  <ChevronRight className="h-4 w-4 text-muted-foreground ml-auto shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
                </Link>
              ) : (
                <div className="flex items-center gap-3 p-2 -mx-2">
                  <div className="h-9 w-9 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                    <Building2 className="h-4 w-4 text-primary" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate">{task.propertyName}</p>
                    {task.propertyAddress && (
                      <p className="text-[11px] text-muted-foreground truncate">{task.propertyAddress}</p>
                    )}
                  </div>
                </div>
              )}
            </Card>
          )}

          {/* Unit */}
          {task.unitLabel && (
            <Card className="p-4 space-y-2">
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
                <Home className="h-3.5 w-3.5" />
                Unit
              </h3>
              <div className="flex items-center gap-3 p-2 -mx-2">
                <div className="h-9 w-9 rounded-lg bg-muted flex items-center justify-center shrink-0">
                  <Home className="h-4 w-4 text-muted-foreground" />
                </div>
                <p className="text-sm font-medium">{task.unitLabel}</p>
              </div>
            </Card>
          )}

          {/* Tenant */}
          {(task.tenantName || propertyTenants.length > 0) && (
            <Card className="p-4 space-y-2">
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
                <User className="h-3.5 w-3.5" />
                Tenant{propertyTenants.length > 1 ? 's' : ''}
              </h3>
              {task.tenantName ? (
                <div className="flex items-center gap-3 p-2 -mx-2">
                  <div className="h-9 w-9 rounded-lg bg-green-500/10 flex items-center justify-center shrink-0">
                    <User className="h-4 w-4 text-green-600 dark:text-green-400" />
                  </div>
                  <p className="text-sm font-medium">{task.tenantName}</p>
                </div>
              ) : (
                propertyTenants.map(t => (
                  <Link
                    key={t.id}
                    to={`/tenants/${t.id}`}
                    className="flex items-center gap-3 rounded-lg p-2 -mx-2 hover:bg-muted transition-colors group"
                  >
                    <div className="h-9 w-9 rounded-lg bg-green-500/10 flex items-center justify-center shrink-0">
                      <User className="h-4 w-4 text-green-600 dark:text-green-400" />
                    </div>
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate group-hover:text-primary transition-colors">{t.name}</p>
                      <p className="text-[11px] text-muted-foreground">{t.unit}</p>
                    </div>
                    <ChevronRight className="h-4 w-4 text-muted-foreground ml-auto shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
                  </Link>
                ))
              )}
            </Card>
          )}

          {/* Vendor */}
          {(assignedVendor || task.assignedVendorName) && (
            <Card className="p-4 space-y-2">
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
                <Wrench className="h-3.5 w-3.5" />
                Vendor
              </h3>
              <div className="flex items-center gap-3 p-2 -mx-2">
                <div className="h-9 w-9 rounded-lg bg-orange-500/10 flex items-center justify-center shrink-0">
                  <Wrench className="h-4 w-4 text-orange-600 dark:text-orange-400" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate">
                    {assignedVendor?.name ?? task.assignedVendorName}
                  </p>
                  {assignedVendor?.company && (
                    <p className="text-[11px] text-muted-foreground truncate">{assignedVendor.company}</p>
                  )}
                  {assignedVendor?.vendorType && (
                    <Badge variant="secondary" className="text-[10px] mt-1">{assignedVendor.vendorType}</Badge>
                  )}
                </div>
              </div>
            </Card>
          )}

          {/* Linked Conversations summary */}
          <Card className="p-4 space-y-2">
            <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
              <MessageCircle className="h-3.5 w-3.5" />
              Linked Chats
            </h3>
            <div className="space-y-1.5">
              {task.aiConversationId && (
                <button
                  onClick={() => openChat({ taskId: task.id })}
                  className="w-full flex items-center gap-2.5 p-2 -mx-2 rounded-lg hover:bg-muted transition-colors text-left"
                >
                  <Bot className="h-4 w-4 text-primary shrink-0" />
                  <div className="min-w-0">
                    <p className="text-sm font-medium">AI Conversation</p>
                    <p className="text-[11px] text-muted-foreground">{task.chatThread.length} messages</p>
                  </div>
                </button>
              )}
              {task.externalConversationId && (
                <button
                  onClick={() => openChat({ conversationId: task.externalConversationId! })}
                  className="w-full flex items-center gap-2.5 p-2 -mx-2 rounded-lg hover:bg-muted transition-colors text-left"
                >
                  <User className="h-4 w-4 text-green-600 dark:text-green-400 shrink-0" />
                  <div className="min-w-0">
                    <p className="text-sm font-medium">External Chat</p>
                    <p className="text-[11px] text-muted-foreground">
                      {externalMessages.length > 0 ? `${externalMessages.length} messages` : 'Tenant/Vendor thread'}
                    </p>
                  </div>
                </button>
              )}
              {task.parentConversationId && task.parentConversationId !== task.externalConversationId && (
                <button
                  onClick={() => openChat({ conversationId: task.parentConversationId! })}
                  className="w-full flex items-center gap-2.5 p-2 -mx-2 rounded-lg hover:bg-muted transition-colors text-left"
                >
                  <MessageCircle className="h-4 w-4 text-muted-foreground shrink-0" />
                  <div className="min-w-0">
                    <p className="text-sm font-medium">Parent Conversation</p>
                    <p className="text-[11px] text-muted-foreground">Spawned from this chat</p>
                  </div>
                </button>
              )}
              {!task.aiConversationId && !task.externalConversationId && !task.parentConversationId && (
                <p className="text-sm text-muted-foreground py-2">No linked conversations.</p>
              )}
            </div>
          </Card>

          {/* Task Meta */}
          <Card className="p-4 space-y-2">
            <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
              <Clock className="h-3.5 w-3.5" />
              Details
            </h3>
            <dl className="space-y-2 text-sm">
              {task.priority && (
                <div className="flex justify-between">
                  <dt className="text-muted-foreground">Priority</dt>
                  <dd className="font-medium capitalize">{task.priority}</dd>
                </div>
              )}
              {task.source && (
                <div className="flex justify-between">
                  <dt className="text-muted-foreground">Source</dt>
                  <dd className="font-medium capitalize">{task.source}</dd>
                </div>
              )}
              {task.resolvedAt && (
                <div className="flex justify-between">
                  <dt className="text-muted-foreground">Resolved</dt>
                  <dd className="font-medium">{formatDistanceToNow(task.resolvedAt, { addSuffix: true })}</dd>
                </div>
              )}
            </dl>
          </Card>
        </div>
      </div>
    </div>
  );
};

export default TaskCommandCenter;
