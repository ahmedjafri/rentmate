import { useState, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Checkbox } from '@/components/ui/checkbox';
import { Button } from '@/components/ui/button';
import {
  Bot, CheckCircle2,
  PauseCircle, Zap, ShieldCheck, Hand, Lock, XCircle, ChevronDown, Search,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { formatMessageTime } from '@/components/chat/ChatMessage';
import { TaskMode, SuggestionCategory, categoryColors, categoryLabels } from '@/data/mockData';
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
  const { actionDeskTasks, properties, openChat, chatPanel, isLoading } = useApp();
  const [statusFilters, setStatusFilters] = useState<StatusFilter[]>([]);
  const [categoryFilters, setCategoryFilters] = useState<SuggestionCategory[]>([]);
  const [propertyFilters, setPropertyFilters] = useState<string[]>([]);
  const [search, setSearch] = useState('');
  const [searchParams, setSearchParams] = useSearchParams();
  const hasRestoredRef = useRef(false);

  // Restore open chat from URL once data has loaded
  useEffect(() => {
    if (isLoading) return;
    const taskId = searchParams.get('task');
    if (taskId) openChat({ taskId });
    hasRestoredRef.current = true;
  }, [isLoading]); // eslint-disable-line react-hooks/exhaustive-deps

  // Sync URL when chat panel opens/closes (skip until initial restore has run)
  useEffect(() => {
    if (!hasRestoredRef.current) return;
    if (chatPanel.isOpen && chatPanel.taskId) {
      setSearchParams({ task: chatPanel.taskId }, { replace: true });
    } else {
      setSearchParams({}, { replace: true });
    }
  }, [chatPanel.isOpen, chatPanel.taskId]); // eslint-disable-line react-hooks/exhaustive-deps

  const q = search.trim().toLowerCase();

  const taskMatch = (t: typeof actionDeskTasks[0]) => {
    if (categoryFilters.length > 0 && !categoryFilters.includes(t.category)) return false;
    if (propertyFilters.length > 0 && !propertyFilters.includes(t.propertyId ?? '')) return false;
    if (q) {
      const property = t.propertyId ? properties.find(p => p.id === t.propertyId) : null;
      const propertyLabel = property ? (property.name || property.address).toLowerCase() : '';
      const tenantNames = t.participants
        .filter(p => p.type === 'tenant' || p.type === 'vendor')
        .map(p => p.name.toLowerCase())
        .join(' ');
      if (!t.title.toLowerCase().includes(q) && !propertyLabel.includes(q) && !tenantNames.includes(q)) return false;
    }
    return true;
  };

  const allNeedsAttention = actionDeskTasks.filter(t => t.status === 'active' && (t.mode === 'waiting_approval' || t.mode === 'manual'));
  const allAutonomous = actionDeskTasks.filter(t => t.status === 'active' && t.mode === 'autonomous');
  const allCompleted = actionDeskTasks.filter(t => t.status !== 'active');

  const showAll = statusFilters.length === 0;
  const needsAttention = (showAll || statusFilters.includes('needs_attention') ? allNeedsAttention : []).filter(taskMatch);
  const autonomous = (showAll || statusFilters.includes('autonomous') ? allAutonomous : []).filter(taskMatch);
  const completed = (showAll || statusFilters.includes('completed') ? allCompleted : []).filter(taskMatch);

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

  // Only show properties that have at least one task
  const taskPropertyIds = new Set(actionDeskTasks.map(t => t.propertyId).filter(Boolean));
  const propertyOptions = properties
    .filter(p => taskPropertyIds.has(p.id))
    .map(p => ({ value: p.id, label: p.name || p.address }));

  const renderTaskCard = (task: typeof actionDeskTasks[0]) => {
    const mode = getModeBadge(task);
    const ModeIcon = mode.icon;
    const property = task.propertyId ? properties.find(p => p.id === task.propertyId) : null;

    return (
      <Card key={task.id} className="px-3 py-2.5 rounded-xl hover:shadow-md transition-shadow cursor-pointer" onClick={() => openChat({ taskId: task.id })}>
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 flex-wrap min-w-0">
            <Badge variant="secondary" className={cn('text-[10px] rounded-lg gap-1 shrink-0', mode.className)}>
              <ModeIcon className="h-3 w-3" />
              {mode.label}
            </Badge>
            <Badge variant="secondary" className={cn('text-[10px] rounded-lg shrink-0', categoryColors[task.category])}>
              {categoryLabels[task.category]}
            </Badge>
            {task.unreadCount > 0 && (
              <Badge className="h-4 px-1.5 text-[10px] bg-primary text-primary-foreground shrink-0">
                {task.unreadCount} new
              </Badge>
            )}
            {task.confidential && (
              <Badge variant="secondary" className="text-[10px] rounded-lg gap-1 bg-destructive/10 text-destructive shrink-0">
                <Lock className="h-3 w-3" />
                Confidential
              </Badge>
            )}
          </div>
          <span className="text-[10px] text-muted-foreground shrink-0">{formatMessageTime(task.lastMessageAt)}</span>
        </div>

        <div className="flex items-center justify-between gap-2 mt-1.5">
          <h3 className="font-medium text-sm truncate">{task.title}</h3>
          {property && (
            <span className="text-[10px] text-muted-foreground shrink-0">{property.name || property.address}</span>
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
        <div className="flex items-center gap-2 flex-wrap justify-end">
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
          {propertyOptions.length > 0 && (
            <MultiSelect
              options={propertyOptions}
              selected={propertyFilters}
              onChange={setPropertyFilters}
              placeholder="All Properties"
              width="w-44"
            />
          )}
        </div>
      </div>

      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
        <Input
          className="h-8 pl-8 text-sm rounded-lg"
          placeholder="Search by task, tenant, or property…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
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
