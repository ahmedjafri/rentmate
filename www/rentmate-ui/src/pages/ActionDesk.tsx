import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Checkbox } from '@/components/ui/checkbox';
import { Button } from '@/components/ui/button';
import {
  Bot, User, Wrench, MessageCircle, CheckCircle2,
  PauseCircle, Zap, ShieldCheck, Hand, Lock, XCircle, ChevronDown,
} from 'lucide-react';
import { TaskMode, TaskParticipantType, SuggestionCategory, categoryColors, categoryLabels, urgencyColors } from '@/data/mockData';
import { cn } from '@/lib/utils';

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

const participantIcon: Record<TaskParticipantType, React.ElementType> = {
  agent: Bot,
  tenant: User,
  vendor: Wrench,
  manager: User,
};

type StatusFilter = 'needs_attention' | 'autonomous' | 'completed';

interface MultiSelectProps<T extends string> {
  options: { value: T; label: string }[];
  selected: T[];
  onChange: (selected: T[]) => void;
  placeholder: string;
  width?: string;
}

function MultiSelect<T extends string>({ options, selected, onChange, placeholder, width = 'w-44' }: MultiSelectProps<T>) {
  const toggle = (value: T) => {
    onChange(selected.includes(value) ? selected.filter(v => v !== value) : [...selected, value]);
  };

  const label = selected.length === 0
    ? placeholder
    : selected.length === 1
    ? options.find(o => o.value === selected[0])?.label ?? placeholder
    : `${selected.length} selected`;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className={cn('h-8 text-xs justify-between font-normal', width)}>
          <span className="truncate">{label}</span>
          <ChevronDown className="h-3 w-3 ml-1 shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="p-1 w-48" align="end">
        {options.map(({ value, label: optLabel }) => (
          <div
            key={value}
            className="flex items-center gap-2 px-2 py-1.5 rounded-sm hover:bg-muted cursor-pointer"
            onClick={() => toggle(value)}
          >
            <Checkbox
              checked={selected.includes(value)}
              onCheckedChange={() => toggle(value)}
              className="h-3.5 w-3.5"
            />
            <span className="text-xs">{optLabel}</span>
          </div>
        ))}
      </PopoverContent>
    </Popover>
  );
}

const ActionDesk = () => {
  const { actionDeskTasks, properties, openChat, closeChat, chatPanel } = useApp();
  const [statusFilters, setStatusFilters] = useState<StatusFilter[]>([]);
  const [categoryFilters, setCategoryFilters] = useState<SuggestionCategory[]>([]);
  const [searchParams, setSearchParams] = useSearchParams();

  // Restore open chat from URL on mount
  useEffect(() => {
    const taskId = searchParams.get('task');
    if (taskId) openChat({ taskId });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Sync URL when chat panel opens/closes
  useEffect(() => {
    if (chatPanel.isOpen && chatPanel.taskId) {
      setSearchParams({ task: chatPanel.taskId }, { replace: true });
    } else {
      setSearchParams({}, { replace: true });
    }
  }, [chatPanel.isOpen, chatPanel.taskId]); // eslint-disable-line react-hooks/exhaustive-deps

  const categoryMatch = (t: typeof actionDeskTasks[0]) =>
    categoryFilters.length === 0 || categoryFilters.includes(t.category);

  const allNeedsAttention = actionDeskTasks.filter(t => t.status === 'active' && (t.mode === 'waiting_approval' || t.mode === 'manual'));
  const allAutonomous = actionDeskTasks.filter(t => t.status === 'active' && t.mode === 'autonomous');
  const allCompleted = actionDeskTasks.filter(t => t.status !== 'active');

  const showAll = statusFilters.length === 0;
  const needsAttention = (showAll || statusFilters.includes('needs_attention') ? allNeedsAttention : []).filter(categoryMatch);
  const autonomous = (showAll || statusFilters.includes('autonomous') ? allAutonomous : []).filter(categoryMatch);
  const completed = (showAll || statusFilters.includes('completed') ? allCompleted : []).filter(categoryMatch);

  const needsAttentionCount = allNeedsAttention.length;
  const activeCount = actionDeskTasks.filter(t => t.status === 'active').length;

  const statusOptions: { value: StatusFilter; label: string }[] = [
    { value: 'needs_attention', label: needsAttentionCount > 0 ? `Needs Attention (${needsAttentionCount})` : 'Needs Attention' },
    { value: 'autonomous', label: 'Autonomous' },
    { value: 'completed', label: 'Completed' },
  ];

  const categoryOptions: { value: SuggestionCategory; label: string }[] = [
    { value: 'rent', label: 'Rent & Payments' },
    { value: 'maintenance', label: 'Maintenance' },
    { value: 'leasing', label: 'Leasing' },
    { value: 'compliance', label: 'Compliance' },
  ];

  const renderTaskCard = (task: typeof actionDeskTasks[0]) => {
    const mode = getModeBadge(task);
    const ModeIcon = mode.icon;
    const property = task.propertyId ? properties.find(p => p.id === task.propertyId) : null;

    return (
      <Card key={task.id} className="p-4 rounded-xl hover:shadow-md transition-shadow cursor-pointer" onClick={() => openChat({ taskId: task.id })}>
        <div className="flex items-start justify-between gap-3 mb-2">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge variant="secondary" className={cn('text-[10px] rounded-lg gap-1', mode.className)}>
              <ModeIcon className="h-3 w-3" />
              {mode.label}
            </Badge>
            <Badge variant="secondary" className={cn('text-[10px] rounded-lg', categoryColors[task.category])}>
              {categoryLabels[task.category]}
            </Badge>
            {task.unreadCount > 0 && (
              <Badge className="h-4 px-1.5 text-[10px] bg-primary text-primary-foreground">
                {task.unreadCount} new
              </Badge>
            )}
            {task.confidential && (
              <Badge variant="secondary" className="text-[10px] rounded-lg gap-1 bg-destructive/10 text-destructive">
                <Lock className="h-3 w-3" />
                Confidential
              </Badge>
            )}
          </div>
        </div>

        <h3 className="font-semibold text-sm mb-1">{task.title}</h3>

        <div className="flex items-start gap-2 mt-2 bg-muted/40 rounded-lg p-2.5">
          <MessageCircle className="h-3 w-3 text-muted-foreground mt-0.5 shrink-0" />
          <div className="min-w-0">
            <span className="text-[11px] font-medium text-muted-foreground">{task.lastMessageBy}</span>
            <p className="text-xs text-foreground line-clamp-2">{task.lastMessage}</p>
          </div>
        </div>

        <div className="mt-2.5 flex items-center justify-between">
          <div className="flex items-center gap-1">
            {task.participants.map((p, i) => {
              const Icon = participantIcon[p.type];
              return (
                <div
                  key={i}
                  className={cn(
                    'flex h-5 w-5 items-center justify-center rounded-full text-[10px]',
                    p.type === 'agent' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground'
                  )}
                  title={`${p.name} (${p.type})`}
                >
                  <Icon className="h-3 w-3" />
                </div>
              );
            })}
            <span className="text-[10px] text-muted-foreground ml-1">
              {task.participants.map(p => p.name.split(' ')[0]).join(', ')}
            </span>
          </div>
          {property && (
            <span className="text-[10px] text-muted-foreground">{property.name}</span>
          )}
        </div>
      </Card>
    );
  };

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Action Desk</h1>
          <p className="text-sm text-muted-foreground">
            {activeCount} active · {needsAttentionCount} need attention
          </p>
        </div>
        <div className="flex items-center gap-2">
          <MultiSelect
            options={statusOptions}
            selected={statusFilters}
            onChange={setStatusFilters}
            placeholder="All Statuses"
            width="w-40"
          />
          <MultiSelect
            options={categoryOptions}
            selected={categoryFilters}
            onChange={setCategoryFilters}
            placeholder="All Categories"
            width="w-44"
          />
        </div>
      </div>

      {/* Needs Attention */}
      {needsAttention.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
            <ShieldCheck className="h-3.5 w-3.5 text-warning" />
            Needs Attention · {needsAttention.length}
          </h2>
          {needsAttention.map(task => renderTaskCard(task))}
        </div>
      )}

      {/* Autonomous */}
      {autonomous.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
            <Zap className="h-3.5 w-3.5 text-accent" />
            Running Autonomously · {autonomous.length}
          </h2>
          {autonomous.map(task => renderTaskCard(task))}
        </div>
      )}

      {/* Completed */}
      {completed.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
            <CheckCircle2 className="h-3.5 w-3.5 text-muted-foreground" />
            Completed · {completed.length}
          </h2>
          {completed.map(task => {
            const completedMode = getModeBadge(task);
            const CompletedModeIcon = completedMode.icon;
            const StatusIcon = task.status === 'resolved' ? CheckCircle2 : task.status === 'cancelled' ? XCircle : PauseCircle;
            return (
              <Card key={task.id} className="p-4 rounded-xl opacity-70 cursor-pointer hover:opacity-85 transition-opacity" onClick={() => openChat({ taskId: task.id })}>
                <div className="flex items-start justify-between gap-3 mb-1">
                  <div className="flex items-center gap-2">
                    <StatusIcon className={cn('h-4 w-4', task.status === 'resolved' ? 'text-accent' : task.status === 'cancelled' ? 'text-destructive' : 'text-muted-foreground')} />
                    <h3 className="font-medium text-sm">{task.title}</h3>
                  </div>
                  <Badge variant="secondary" className={cn('text-[10px] rounded-lg gap-1', completedMode.className)}>
                    <CompletedModeIcon className="h-3 w-3" />
                    {completedMode.label}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground ml-6">{task.lastMessage}</p>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default ActionDesk;
