import { useState, useEffect, useRef } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { useApp } from '@/context/AppContext';
import { Property, Tenant, ActionDeskTask, Vendor } from '@/data/mockData';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Checkbox } from '@/components/ui/checkbox';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  Bot, CheckCircle2, PauseCircle, Zap, ShieldCheck, Hand, Lock, XCircle,
  ChevronDown, X, Building2, User, MessageCircle, Wrench,
} from 'lucide-react';
import { PageLoader } from '@/components/ui/page-loader';
import { formatMessageTime } from '@/components/chat/ChatMessage';
import { TaskMode, SuggestionCategory, SuggestionUrgency, categoryColors, categoryLabels } from '@/data/mockData';
import { cn } from '@/lib/utils';
import { graphqlQuery, ASSIGN_VENDOR_TO_TASK_MUTATION } from '@/data/api';
import { toast } from 'sonner';

// ─── Mode badge ───────────────────────────────────────────────────────────────

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

// ─── MultiSelect ──────────────────────────────────────────────────────────────

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
            <Checkbox checked={selected.includes(value)} onCheckedChange={() => toggle(value)} className="h-3.5 w-3.5" />
            <span className="text-xs">{optLabel}</span>
          </div>
        ))}
      </PopoverContent>
    </Popover>
  );
}

// ─── SmartSearch ──────────────────────────────────────────────────────────────

interface SearchChip {
  id: string;
  type: 'property' | 'tenant' | 'text';
  label: string;
  value: string;
}

interface SmartSearchProps {
  chips: SearchChip[];
  onChipsChange: (chips: SearchChip[]) => void;
  tasks: ActionDeskTask[];
  properties: Property[];
  tenants: Tenant[];
}

function SmartSearch({ chips, onChipsChange, tasks, properties, tenants }: SmartSearchProps) {
  const [input, setInput] = useState('');
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const q = input.trim().toLowerCase();

  // Build suggestions
  const existingPropertyIds = new Set(chips.filter(c => c.type === 'property').map(c => c.value));
  const existingTenantNames = new Set(chips.filter(c => c.type === 'tenant').map(c => c.value));
  const taskPropertyIds = new Set(tasks.map(t => t.propertyId).filter(Boolean) as string[]);

  const propertySuggestions = properties
    .filter(p => taskPropertyIds.has(p.id) && !existingPropertyIds.has(p.id))
    .filter(p => !q || (p.name || p.address).toLowerCase().includes(q))
    .slice(0, 5)
    .map(p => ({ type: 'property' as const, label: p.name || p.address, value: p.id, sublabel: p.name ? p.address : undefined }));

  const tenantNameMap = new Map<string, string>();
  // Include all tenants from context, not just task participants
  tenants.forEach(t => { if (!tenantNameMap.has(t.name)) tenantNameMap.set(t.name, t.name); });
  tasks.forEach(t => {
    t.participants
      .filter(p => p.type === 'tenant' || p.type === 'vendor')
      .forEach(p => { if (!tenantNameMap.has(p.name)) tenantNameMap.set(p.name, p.name); });
  });
  const tenantSuggestions = [...tenantNameMap.keys()]
    .filter(name => !existingTenantNames.has(name) && (!q || name.toLowerCase().includes(q)))
    .slice(0, 5)
    .map(name => ({ type: 'tenant' as const, label: name, value: name, sublabel: undefined }));

  const hasSuggestions = propertySuggestions.length > 0 || tenantSuggestions.length > 0;

  const addChip = (type: SearchChip['type'], label: string, value: string) => {
    onChipsChange([...chips, { id: `${type}-${value}-${Date.now()}`, type, label, value }]);
    setInput('');
    setOpen(false);
    inputRef.current?.focus();
  };

  const removeChip = (id: string) => onChipsChange(chips.filter(c => c.id !== id));

  return (
    <div className="relative">
      <div
        className="flex flex-wrap items-center gap-1.5 min-h-9 px-2.5 py-1.5 rounded-lg border bg-background cursor-text focus-within:ring-1 focus-within:ring-ring"
        onClick={() => inputRef.current?.focus()}
      >
        {chips.map(chip => (
          <span
            key={chip.id}
            className={cn(
              'inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-md text-xs font-medium shrink-0',
              chip.type === 'property' && 'bg-primary/10 text-primary',
              chip.type === 'tenant' && 'bg-green-800 text-green-100',
              chip.type === 'text' && 'bg-muted text-muted-foreground',
            )}
          >
            {chip.type === 'property' && <Building2 className="h-3 w-3 shrink-0" />}
            {chip.type === 'tenant' && <User className="h-3 w-3 shrink-0" />}
            {chip.label}
            <button
              type="button"
              onClick={e => { e.stopPropagation(); removeChip(chip.id); }}
              className="ml-0.5 rounded hover:opacity-70"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          className="flex-1 min-w-32 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          placeholder={chips.length === 0 ? 'Search by task, tenant, or property...' : 'Add filter...'}
          value={input}
          onChange={e => { setInput(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={e => {
            if (e.key === 'Enter' && input.trim()) {
              e.preventDefault();
              addChip('text', input.trim(), input.trim());
            }
            if (e.key === 'Backspace' && !input && chips.length > 0) {
              removeChip(chips[chips.length - 1].id);
            }
            if (e.key === 'Escape') setOpen(false);
          }}
        />
      </div>

      {open && hasSuggestions && (
        <div className="absolute z-50 top-full mt-1 w-full rounded-lg border bg-card shadow-md overflow-hidden">
          {propertySuggestions.length > 0 && (
            <div>
              <p className="px-3 pt-2 pb-1 text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">Properties</p>
              {propertySuggestions.map(s => (
                <button
                  key={s.value}
                  type="button"
                  className="w-full text-left px-3 py-1.5 text-sm hover:bg-muted flex items-center gap-2"
                  onMouseDown={e => { e.preventDefault(); addChip('property', s.label, s.value); }}
                >
                  <Building2 className="h-3.5 w-3.5 text-primary shrink-0" />
                  <span className="flex-1 truncate">{s.label}</span>
                  {s.sublabel && <span className="text-xs text-muted-foreground truncate max-w-32">{s.sublabel}</span>}
                </button>
              ))}
            </div>
          )}
          {tenantSuggestions.length > 0 && (
            <div className={cn(propertySuggestions.length > 0 && 'border-t')}>
              <p className="px-3 pt-2 pb-1 text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">People</p>
              {tenantSuggestions.map(s => (
                <button
                  key={s.value}
                  type="button"
                  className="w-full text-left px-3 py-1.5 text-sm hover:bg-muted flex items-center gap-2"
                  onMouseDown={e => { e.preventDefault(); addChip('tenant', s.label, s.value); }}
                >
                  <User className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                  <span>{s.label}</span>
                </button>
              ))}
            </div>
          )}
          <div className="border-t px-3 py-1.5">
            <p className="text-[11px] text-muted-foreground">Press Enter to search for "{input || '...'}"</p>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Tasks ───────────────────────────────────────────────────────────────────

const Tasks = () => {
  const navigate = useNavigate();
  const { actionDeskTasks, properties, tenants, vendors, openChat, chatPanel, isLoading, updateTask } = useApp();
  const [statusFilters, setStatusFilters] = useState<StatusFilter[]>([]);
  const [categoryFilters, setCategoryFilters] = useState<SuggestionCategory[]>([]);
  const [chips, setChips] = useState<SearchChip[]>([]);
  const [showAllCompleted, setShowAllCompleted] = useState(false);
  const COMPLETED_PREVIEW = 3;
  const [searchParams, setSearchParams] = useSearchParams();
  const hasRestoredRef = useRef(false);

  // Vendor assignment dialog state
  const [vendorDialogTask, setVendorDialogTask] = useState<ActionDeskTask | null>(null);
  const [vendorSearch, setVendorSearch] = useState('');
  const [assigningVendor, setAssigningVendor] = useState(false);

  const handleAssignVendor = async (task: ActionDeskTask, vendor: Vendor) => {
    setAssigningVendor(true);
    try {
      await graphqlQuery(ASSIGN_VENDOR_TO_TASK_MUTATION, { taskId: task.id, vendorId: vendor.id });
      updateTask(task.id, { assignedVendorId: vendor.id, assignedVendorName: vendor.name });
      setVendorDialogTask(null);
      toast.success(`Assigned ${vendor.name} to task`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to assign vendor');
    } finally {
      setAssigningVendor(false);
    }
  };

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

  const taskMatch = (t: ActionDeskTask) => {
    if (categoryFilters.length > 0 && !categoryFilters.includes(t.category)) return false;

    const propertyChips = chips.filter(c => c.type === 'property');
    const tenantChips = chips.filter(c => c.type === 'tenant');
    const textChips = chips.filter(c => c.type === 'text');

    if (propertyChips.length > 0 && !propertyChips.some(c => c.value === t.propertyId)) return false;

    if (tenantChips.length > 0) {
      const participantNames = t.participants.filter(p => p.type === 'tenant' || p.type === 'vendor').map(p => p.name);
      const propertyTenantNames = t.propertyId
        ? tenants.filter(tn => tn.propertyId === t.propertyId && tn.isActive).map(tn => tn.name)
        : [];
      const allNames = [...participantNames, ...propertyTenantNames];
      if (!tenantChips.some(c => allNames.includes(c.value))) return false;
    }

    if (textChips.length > 0) {
      const property = t.propertyId ? properties.find(p => p.id === t.propertyId) : null;
      const propertyLabel = property ? (property.name || property.address).toLowerCase() : '';
      const tenantNames = t.participants
        .filter(p => p.type === 'tenant' || p.type === 'vendor')
        .map(p => p.name.toLowerCase()).join(' ');
      for (const chip of textChips) {
        const q = chip.value.toLowerCase();
        if (!t.title.toLowerCase().includes(q) && !propertyLabel.includes(q) && !tenantNames.includes(q)) return false;
      }
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

  const renderTaskCard = (task: ActionDeskTask) => {
    const mode = getModeBadge(task);
    const ModeIcon = mode.icon;
    const property = task.propertyId ? properties.find(p => p.id === task.propertyId) : null;

    return (
      <Card key={task.id} className={cn("px-3 py-2.5 rounded-xl hover:shadow-md transition-shadow cursor-pointer", chatPanel.isOpen && chatPanel.taskId === task.id && "ring-2 ring-primary/40")} onClick={() => navigate(`/tasks/${task.id}`)}>
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
          <div className="flex items-center gap-1.5 min-w-0">
            {task.taskNumber != null && (
              <span className="text-[10px] font-mono text-muted-foreground shrink-0">#{task.taskNumber}</span>
            )}
            <h3 className="font-medium text-sm truncate">{task.title}</h3>
          </div>
          {property && (
            <span className="text-[10px] text-muted-foreground shrink-0">{property.name || property.address}</span>
          )}
        </div>
        {task.parentConversationId && (
          <div className="mt-1.5">
            <button
              onClick={(e) => {
                e.stopPropagation();
                openChat({ conversationId: task.parentConversationId! });
              }}
              className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground hover:bg-muted/80 border"
            >
              <MessageCircle className="h-2.5 w-2.5" />
              Spawned from chat
            </button>
          </div>
        )}
        {task.requireVendorType && (
          <div className="mt-1.5">
            {task.assignedVendorName ? (
              <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400 border border-green-200 dark:border-green-800">
                <Wrench className="h-2.5 w-2.5" />
                {task.assignedVendorName}
              </span>
            ) : (
              <button
                onClick={(e) => { e.stopPropagation(); setVendorDialogTask(task); setVendorSearch(''); }}
                className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-orange-50 text-orange-700 dark:bg-orange-900/20 dark:text-orange-400 hover:bg-orange-100 border border-orange-200 dark:border-orange-800"
              >
                <Wrench className="h-2.5 w-2.5" />
                Assign {task.requireVendorType}
              </button>
            )}
          </div>
        )}
      </Card>
    );
  };

  if (isLoading) return <PageLoader />;

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Tasks</h1>
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
        </div>
      </div>

      {/* Vendor Assignment Dialog */}
      {vendorDialogTask && (() => {
        const filteredVendors = vendors.filter(v => {
          const matchesType = !vendorDialogTask.requireVendorType || v.vendorType === vendorDialogTask.requireVendorType;
          const q = vendorSearch.trim().toLowerCase();
          const matchesSearch = !q || v.name.toLowerCase().includes(q) || (v.company ?? '').toLowerCase().includes(q);
          return matchesType && matchesSearch;
        });
        const allVendors = !vendorSearch && filteredVendors.length === 0 ? vendors.filter(v => {
          const q = vendorSearch.trim().toLowerCase();
          return !q || v.name.toLowerCase().includes(q) || (v.company ?? '').toLowerCase().includes(q);
        }) : filteredVendors;

        return (
          <Dialog open onOpenChange={() => setVendorDialogTask(null)}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>
                  Assign {vendorDialogTask.requireVendorType ?? 'Vendor'}
                </DialogTitle>
              </DialogHeader>
              <div className="space-y-3 pt-1">
                <p className="text-sm text-muted-foreground">
                  Task: <span className="font-medium text-foreground">{vendorDialogTask.title}</span>
                </p>
                <Input
                  placeholder="Search vendors..."
                  value={vendorSearch}
                  onChange={e => setVendorSearch(e.target.value)}
                  autoFocus
                />
                <div className="space-y-1.5 max-h-72 overflow-y-auto">
                  {allVendors.length === 0 ? (
                    <p className="text-sm text-muted-foreground text-center py-6">
                      No vendors found.{' '}
                      <a href="/vendors" className="underline" onClick={() => setVendorDialogTask(null)}>
                        Add vendors
                      </a>{' '}
                      first.
                    </p>
                  ) : (
                    allVendors.map(v => (
                      <button
                        key={v.id}
                        disabled={assigningVendor}
                        onClick={() => handleAssignVendor(vendorDialogTask, v)}
                        className="w-full text-left px-3 py-2.5 rounded-lg border hover:bg-muted transition-colors flex items-center justify-between gap-3 disabled:opacity-50"
                      >
                        <div>
                          <div className="text-sm font-medium">{v.name}</div>
                          {v.company && <div className="text-xs text-muted-foreground">{v.company}</div>}
                        </div>
                        {v.vendorType && (
                          <Badge variant="secondary" className="text-[10px] shrink-0">{v.vendorType}</Badge>
                        )}
                      </button>
                    ))
                  )}
                </div>
              </div>
            </DialogContent>
          </Dialog>
        );
      })()}

      <SmartSearch
        chips={chips}
        onChipsChange={setChips}
        tasks={actionDeskTasks}
        properties={properties}
        tenants={tenants}
      />

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
          {(showAllCompleted ? completed : completed.slice(0, COMPLETED_PREVIEW)).map(task => {
            const completedMode = getModeBadge(task);
            const CompletedModeIcon = completedMode.icon;
            const StatusIcon = task.status === 'resolved' ? CheckCircle2 : task.status === 'cancelled' ? XCircle : PauseCircle;
            return (
              <Card key={task.id} className={cn("p-4 rounded-xl opacity-70 cursor-pointer hover:opacity-85 transition-opacity", chatPanel.isOpen && chatPanel.taskId === task.id && "ring-2 ring-primary/40 opacity-100")} onClick={() => navigate(`/tasks/${task.id}`)}>
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
          {completed.length > COMPLETED_PREVIEW && (
            <button
              className="w-full text-xs text-muted-foreground hover:text-foreground py-1.5 transition-colors"
              onClick={() => setShowAllCompleted(s => !s)}
            >
              {showAllCompleted
                ? 'Show less'
                : `Show ${completed.length - COMPLETED_PREVIEW} more completed`}
            </button>
          )}
        </div>
      )}
    </div>
  );
};

export default Tasks;
