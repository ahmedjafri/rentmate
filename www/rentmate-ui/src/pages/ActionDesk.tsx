import { useState } from 'react';
import { useApp } from '@/context/AppContext';
import { Suggestion, SuggestionCategory, categoryColors, categoryLabels } from '@/data/mockData';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Lightbulb, CheckCircle2, XCircle, Loader2, MessageCircle, Send, X, Building2, Wrench } from 'lucide-react';
import { formatMessageTime } from '@/components/chat/ChatMessage';
import { PageLoader } from '@/components/ui/page-loader';

import { actOnSuggestion } from '@/graphql/client';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';

// ─── Urgency styling ─────────────────────────────────────────────────────────

const urgencyColors: Record<string, string> = {
  critical: 'text-destructive',
  high: 'text-orange-600 dark:text-orange-400',
  medium: 'text-yellow-600 dark:text-yellow-400',
  low: 'text-muted-foreground',
};

// ─── SuggestionCard ──────────────────────────────────────────────────────────

export function SuggestionCard({ suggestion, onAction, isActive, compact }: {
  suggestion: Suggestion;
  onAction: (id: string, action: string, editedBody?: string) => Promise<void>;
  isActive?: boolean;
  /** Stack action buttons vertically for narrow containers */
  compact?: boolean;
}) {
  const [loading, setLoading] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const { openChat } = useApp();

  const draftText = suggestion.draftMessage ?? '';
  const [editedDraft, setEditedDraft] = useState(draftText);

  const opts = suggestion.options ?? [
    { key: 'create', label: 'Create Task', action: 'send_and_create_task', variant: 'default' },
    { key: 'dismiss', label: 'Dismiss', action: 'reject_task', variant: 'ghost' },
  ];

  const sendAction = 'send_and_create_task';

  const handleAction = async (action: string, body?: string) => {
    setLoading(action);
    try {
      await onAction(suggestion.id, action, body);
    } finally {
      setLoading(null);
    }
  };

  return (
    <Card className={cn("px-4 py-3 rounded-xl hover:shadow-md transition-shadow space-y-2", isActive && "ring-2 ring-primary/40")}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 flex-wrap min-w-0">
          <Badge variant="secondary" className={cn('text-[10px] rounded-lg shrink-0', categoryColors[suggestion.category])}>
            {categoryLabels[suggestion.category] ?? suggestion.category}
          </Badge>
          {suggestion.urgency && (
            <span className={cn('text-[10px] font-medium', urgencyColors[suggestion.urgency])}>
              {suggestion.urgency}
            </span>
          )}
        </div>
        <span className="text-[10px] text-muted-foreground shrink-0">{formatMessageTime(suggestion.createdAt instanceof Date ? suggestion.createdAt : new Date(suggestion.createdAt))}</span>
      </div>

      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3
            className="font-medium text-sm cursor-pointer hover:underline"
            onClick={() => openChat({ suggestionId: suggestion.id })}
          >
            {suggestion.title}
          </h3>
          {(suggestion.vendorName || suggestion.propertyName) && (
            <div className="flex items-center gap-2 mt-0.5 flex-wrap">
              {suggestion.vendorName && (
                <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                  <Wrench className="h-2.5 w-2.5" />
                  {suggestion.vendorName}
                </span>
              )}
              {suggestion.propertyName && (
                <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                  <Building2 className="h-2.5 w-2.5" />
                  {suggestion.propertyName}
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Draft: read-only preview or editable textarea */}
      {editing ? (
        <div className="space-y-2">
          <Textarea
            value={editedDraft}
            onChange={e => setEditedDraft(e.target.value)}
            className="text-xs min-h-[80px] resize-none"
            autoFocus
          />
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="default"
              className="h-7 text-xs rounded-lg gap-1"
              disabled={!editedDraft.trim() || loading !== null}
              onClick={() => handleAction(sendAction, editedDraft.trim())}
            >
              {loading === sendAction ? <Loader2 className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" />}
              Send
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs rounded-lg gap-1"
              disabled={loading !== null}
              onClick={() => { setEditing(false); setEditedDraft(draftText); }}
            >
              <X className="h-3 w-3" />
              Cancel
            </Button>
          </div>
        </div>
      ) : draftText ? (
        <div className="bg-muted/50 rounded-lg px-3 py-2 text-xs text-muted-foreground">
          <div className="flex items-center gap-1 mb-1">
            <MessageCircle className="h-3 w-3" />
            <span className="font-medium text-foreground/70">Draft message</span>
          </div>
          <p className="line-clamp-2">{draftText}</p>
        </div>
      ) : null}

      {/* Action buttons (hidden while editing) */}
      {!editing && (
        <div className={cn("flex gap-2 pt-1", compact ? "flex-col" : "flex-row items-center")}>
          {opts.map(opt => (
            <Button
              key={opt.key}
              size="sm"
              variant={opt.variant as 'default' | 'outline' | 'ghost'}
              className={cn("h-7 text-xs rounded-lg", compact && "w-full")}
              disabled={loading !== null}
              onClick={() => {
                if (opt.action === 'edit_message') {
                  setEditing(true);
                } else {
                  handleAction(opt.action);
                }
              }}
            >
              {loading === opt.key ? <Loader2 className="h-3 w-3 animate-spin" /> : opt.label}
            </Button>
          ))}
        </div>
      )}
    </Card>
  );
}

// ─── ActionDesk (Suggestions) ────────────────────────────────────────────────

const ActionDesk = () => {
  const { suggestions, updateSuggestionStatus, isLoading, chatPanel, openChat } = useApp();
  const [categoryFilter, setCategoryFilter] = useState<SuggestionCategory | null>(null);

  const pending = suggestions
    .filter(s => s.status === 'pending')
    .filter(s => !categoryFilter || s.category === categoryFilter);

  const acted = suggestions
    .filter(s => s.status !== 'pending')
    .slice(0, 5);

  const handleAction = async (suggestionId: string, action: string, editedBody?: string) => {
    const suggestion = suggestions.find(item => item.id === suggestionId);
    if (action === 'request_file_upload') {
      if (suggestion?.taskId) {
        openChat({ taskId: suggestion.taskId });
        toast.info('Open the task chat and upload the requested file there.');
      } else {
        toast.info('Open the linked suggestion and upload the requested file from the task chat.');
      }
      return;
    }
    try {
      const result = await actOnSuggestion(suggestionId, action, editedBody ?? null);
      const { status, taskId } = result.actOnSuggestion;
      updateSuggestionStatus(suggestionId, status.toLowerCase() as 'accepted' | 'dismissed');
      if (status === 'accepted') {
        toast.success(taskId ? 'Task created' : 'Suggestion accepted');
      } else {
        toast.info('Suggestion dismissed');
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Action failed');
    }
  };

  if (isLoading) return <PageLoader />;

  const categories: SuggestionCategory[] = ['maintenance', 'rent', 'leasing', 'compliance'];

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Suggestions</h1>
          <p className="text-sm text-muted-foreground">
            {pending.length} pending
          </p>
        </div>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant={categoryFilter === null ? 'default' : 'ghost'}
            className="h-7 text-xs rounded-lg"
            onClick={() => setCategoryFilter(null)}
          >
            All
          </Button>
          {categories.map(cat => (
            <Button
              key={cat}
              size="sm"
              variant={categoryFilter === cat ? 'default' : 'ghost'}
              className="h-7 text-xs rounded-lg"
              onClick={() => setCategoryFilter(categoryFilter === cat ? null : cat)}
            >
              {categoryLabels[cat]}
            </Button>
          ))}
        </div>
      </div>

      {/* Pending suggestions */}
      {pending.length > 0 ? (
        <div className="space-y-3">
          <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
            <Lightbulb className="h-3.5 w-3.5 text-yellow-500" />
            Pending Suggestions
          </h2>
          {pending.map(s => (
            <SuggestionCard key={s.id} suggestion={s} onAction={handleAction} isActive={chatPanel.isOpen && chatPanel.suggestionId === s.id} />
          ))}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
          <Lightbulb className="h-12 w-12 mb-3 opacity-40" />
          <p className="font-medium">No pending suggestions</p>
          <p className="text-xs mt-1">Automations will create suggestions here when they detect issues.</p>
        </div>
      )}

      {/* Recently acted */}
      {acted.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
            {acted[0]?.status === 'accepted'
              ? <CheckCircle2 className="h-3.5 w-3.5 text-accent" />
              : <XCircle className="h-3.5 w-3.5 text-muted-foreground" />}
            Recent
          </h2>
          {acted.map(s => (
            <Card key={s.id} className="px-4 py-2.5 rounded-xl opacity-60">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 min-w-0">
                  {s.status === 'accepted'
                    ? <CheckCircle2 className="h-4 w-4 text-accent shrink-0" />
                    : <XCircle className="h-4 w-4 text-muted-foreground shrink-0" />}
                  <h3 className="font-medium text-sm truncate">{s.title}</h3>
                </div>
                <Badge variant="secondary" className="text-[10px] rounded-lg shrink-0">
                  {s.actionTaken ?? s.status}
                </Badge>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
};

export default ActionDesk;
