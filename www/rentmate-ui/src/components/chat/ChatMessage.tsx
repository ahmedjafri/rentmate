import { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import { Bot, User, Eye, Lightbulb, Check, X, Send, Pencil, ChevronDown, ChevronUp, CheckCircle2, XCircle, Zap, Building2, Wrench, BookOpen, ArrowUpRight, Loader2, Expand, FileText } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';
import { getToken } from '@/lib/auth';

function ThinkingChain({ steps, isChain }: { steps: string[]; isChain: boolean }) {
  const [expanded, setExpanded] = useState(false);
  if (!isChain) {
    return (
      <div className="flex items-start gap-2 py-1">
        <Eye className="h-3 w-3 text-muted-foreground mt-0.5 shrink-0" />
        <p className="text-[11px] text-muted-foreground italic leading-relaxed truncate max-w-[280px]" title={steps[0]}>{steps[0]}</p>
      </div>
    );
  }
  return (
    <div className="py-1">
      <button
        onClick={() => setExpanded(v => !v)}
        className="flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
      >
        <Eye className="h-3 w-3 shrink-0" />
        <span className="italic">Thinking ({steps.length} steps)</span>
        {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
      </button>
      {expanded && (
        <ol className="mt-1.5 ml-5 space-y-0.5 list-decimal">
          {steps.map((step, i) => (
            <li key={i} className="text-[11px] text-muted-foreground italic leading-relaxed truncate max-w-[280px]" title={step}>{step}</li>
          ))}
        </ol>
      )}
    </div>
  );
}
import { ChatActionCardLink, ChatMessage as ChatMessageType } from '@/data/mockData';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { useApp } from '@/context/AppContext';

function normalizeAssistantMarkdown(content: string): string {
  if (!content.includes('<') || content.includes('```')) return content;

  const startMatch = content.match(/<!DOCTYPE html>|<html[\s>]|<head[\s>]|<body[\s>]|<style[\s>]/i);
  if (!startMatch || startMatch.index === undefined) return content;

  const start = startMatch.index;
  const prefix = content.slice(0, start).trimEnd();
  const htmlBlock = content.slice(start);
  const closingHtmlIndex = htmlBlock.toLowerCase().lastIndexOf('</html>');

  if (closingHtmlIndex >= 0) {
    const code = htmlBlock.slice(0, closingHtmlIndex + '</html>'.length).trim();
    const suffix = htmlBlock.slice(closingHtmlIndex + '</html>'.length).trimStart();
    return [prefix, `\`\`\`html\n${code}\n\`\`\``, suffix].filter(Boolean).join('\n\n');
  }

  const code = htmlBlock.trim();
  return [prefix, `\`\`\`html\n${code}\n\`\`\``].filter(Boolean).join('\n\n');
}

export function formatMessageTime(date: Date): string {
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const isYesterday = date.toDateString() === yesterday.toDateString();
  const time = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (isToday) return time;
  if (isYesterday) return `Yesterday ${time}`;
  return `${date.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`;
}

function RelatedTaskLink({ taskId, label }: { taskId: string; label: string }) {
  const { openChat } = useApp();
  return (
    <button
      onClick={(e) => { e.stopPropagation(); openChat({ taskId }); }}
      className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:text-primary/80 bg-primary/10 hover:bg-primary/15 rounded-md px-2 py-0.5 transition-colors"
    >
      <ArrowUpRight className="h-3 w-3" />
      {label}
    </button>
  );
}

function ContextBubble({ message, taskId }: { message: ChatMessageType; taskId?: string | null }) {
  const [open, setOpen] = useState(false);
  const [fullContext, setFullContext] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const showFullContext = async () => {
    if (!taskId) return;
    setOpen(true);
    if (fullContext) return; // already loaded
    setLoading(true);
    try {
      const token = getToken();
      const res = await fetch(`/chat/task-context/${taskId}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.ok) {
        const data = await res.json();
        setFullContext(data.context);
      }
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="rounded-xl border border-primary/15 bg-primary/5 p-3 space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <BookOpen className="h-3.5 w-3.5 text-primary" />
            <span className="text-[10px] font-semibold uppercase tracking-wide text-primary">Context</span>
          </div>
          <div className="flex items-center gap-2">
            {taskId && (
              <button
                onClick={showFullContext}
                className="flex items-center gap-1 text-[10px] text-primary/70 hover:text-primary transition-colors"
              >
                <Expand className="h-3 w-3" />
                Full Context
              </button>
            )}
            <span className="text-[10px] text-muted-foreground">{formatMessageTime(message.timestamp)}</span>
          </div>
        </div>
        <p className="text-xs text-foreground leading-relaxed">{message.content}</p>
        {message.relatedTasks && message.relatedTasks.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {message.relatedTasks.map(ref => (
              <RelatedTaskLink key={ref.taskId} taskId={ref.taskId} label={ref.label} />
            ))}
          </div>
        )}
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl max-h-[80vh]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-sm">
              <BookOpen className="h-4 w-4" />
              Agent Context
            </DialogTitle>
          </DialogHeader>
          <ScrollArea className="max-h-[60vh]">
            {loading ? (
              <div className="flex justify-center py-8">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <pre className="text-xs whitespace-pre-wrap font-mono p-4 bg-muted/50 rounded-lg leading-relaxed">
                {fullContext || 'No context available'}
              </pre>
            )}
          </ScrollArea>
        </DialogContent>
      </Dialog>
    </>
  );
}

function ActionCardBubble({ message }: { message: ChatMessageType }) {
  const navigate = useNavigate();
  const card = message.actionCard;

  if (!card) return null;

  const kindConfig = {
    suggestion: { icon: Lightbulb, label: 'Suggestion created', className: 'text-primary', badge: 'bg-primary/10 text-primary border-primary/20' },
    property: { icon: Building2, label: 'Property created', className: 'text-blue-700 dark:text-blue-400', badge: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 border-blue-200 dark:border-blue-800' },
    tenant: { icon: User, label: 'Tenant created', className: 'text-emerald-700 dark:text-emerald-400', badge: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800' },
  } as const;

  const cfg = kindConfig[card.kind];
  const Icon = cfg.icon;
  const summary = card.summary?.trim();

  const openLink = (link: ChatActionCardLink) => {
    if (link.entityType === 'suggestion') {
      navigate(`/action-desk?suggestion=${link.entityId}`);
      return;
    }
    if (link.entityType === 'tenant') {
      navigate(`/tenants/${link.entityId}`);
      return;
    }
    if (link.entityType === 'property') {
      navigate(`/properties/${link.entityId}`);
      return;
    }
    if (link.entityType === 'unit') {
      navigate(`/properties/${link.propertyId ?? ''}?unit=${link.entityId}#unit-${link.entityId}`);
    }
  };

  return (
    <div className="rounded-lg border border-primary/15 bg-card px-2.5 py-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex items-start gap-2">
          <div className={cn('mt-0.5 flex h-6 w-6 items-center justify-center rounded-md bg-muted/60', cfg.className)}>
            <Icon className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <p className="text-xs font-semibold leading-tight">{card.title}</p>
              <Badge variant="outline" className={cn('h-5 rounded-md px-1.5 text-[9px]', cfg.badge)}>
                {cfg.label}
              </Badge>
            </div>
            {summary && (
              <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-muted-foreground">{summary}</p>
            )}
          </div>
        </div>
        <span className="pt-0.5 text-[10px] text-muted-foreground shrink-0">{formatMessageTime(message.timestamp)}</span>
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5">
        {card.fields?.map((field) => (
          <div
            key={`${field.label}-${field.value}`}
            className="inline-flex max-w-full items-center gap-1 rounded-md bg-muted/50 px-2 py-1 text-[11px] leading-none"
          >
            <span className="font-medium text-muted-foreground">{field.label}:</span>
            <span className="truncate font-medium">{field.value}</span>
          </div>
        ))}

        {card.units?.map((unit) => (
          <button
            key={unit.uid}
            onClick={() => openLink({ label: unit.label, entityType: 'unit', entityId: unit.uid, propertyId: unit.propertyId })}
            className="inline-flex items-center gap-1 rounded-md bg-muted/60 px-2 py-1 text-[11px] font-medium hover:bg-muted"
          >
            <Building2 className="h-3 w-3" />
            {unit.label}
            <ArrowUpRight className="h-3 w-3 opacity-60" />
          </button>
        ))}

        {card.links?.map((link) => (
          <button
            key={`${link.entityType}-${link.entityId}-${link.label}`}
            onClick={() => openLink(link)}
            className="inline-flex items-center gap-1 rounded-md bg-primary/10 px-2 py-1 text-[11px] font-medium text-primary transition-colors hover:bg-primary/15 hover:text-primary/80"
          >
            <ArrowUpRight className="h-3 w-3" />
            {link.label}
          </button>
        ))}
      </div>
    </div>
  );
}

interface Props {
  message: ChatMessageType;
  onApprove?: (messageId: string) => void;
  onReject?: (messageId: string) => void;
  onEdit?: (messageId: string) => void;
  onApprovalAction?: (messageId: string, action: string, editedBody?: string) => Promise<void> | void;
  onSuggestionClick?: (suggestionId: string) => void;
  taskId?: string | null;
}

export function ChatMessageBubble({ message, onApprove, onReject, onEdit, onApprovalAction, onSuggestionClick, taskId }: Props) {
  const navigate = useNavigate();
  const isAssistant = message.role === 'assistant';
  const msgType = message.messageType || 'message';
  const senderType = message.senderType || (isAssistant ? 'ai' : 'manager');
  const [draftExpanded, setDraftExpanded] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [editText, setEditText] = useState('');
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const editRef = useRef<HTMLTextAreaElement>(null);
  const [detailsExpanded, setDetailsExpanded] = useState(
    !message.approvalStatus || message.approvalStatus === 'pending'
  );

  const isManager = senderType === 'manager';
  const isAI = senderType === 'ai';
  const isOther = senderType === 'tenant' || senderType === 'vendor';
  const renderedContent = isAI ? normalizeAssistantMarkdown(message.content) : message.content;

  // Context message — opening summary with optional cross-references
  if (msgType === 'context') {
    return <ContextBubble message={message} taskId={taskId} />;
  }

  if (isAssistant && msgType === 'action') {
    return <ActionCardBubble message={message} />;
  }

  // Error message — red warning style, not a normal AI bubble
  if (msgType === 'error') {
    return (
      <div className="flex items-start gap-2.5">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-destructive/15 text-destructive border border-destructive/25">
          <XCircle className="h-3.5 w-3.5" />
        </div>
        <div className="max-w-[85%] min-w-0 overflow-hidden rounded-2xl rounded-tl-md px-4 py-2.5 text-sm bg-destructive/10 border border-destructive/20">
          <p className="text-[10px] font-semibold mb-1 text-destructive">Error</p>
          <p className="text-sm text-foreground break-words">{message.content}</p>
          <div className="flex items-center justify-between mt-1.5">
            <a
              href="https://github.com/ahmedjafri/rentmate/issues"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[11px] text-destructive/70 hover:text-destructive underline underline-offset-2"
            >
              Report issue
            </a>
            <span className="text-[10px] text-muted-foreground">{formatMessageTime(message.timestamp)}</span>
          </div>
        </div>
      </div>
    );
  }

  // Internal AI notes — compact, muted, full-width
  if (isAssistant && msgType === 'internal') {
    const steps = message.content.split('\n').filter(Boolean);
    const isThinkingChain = steps.length > 1;
    return (
      <ThinkingChain steps={steps} isChain={isThinkingChain} />
    );
  }

  // Suggestion / approval messages — card with link to the suggestion
  if (isAssistant && (msgType === 'suggestion' || msgType === 'approval')) {
    const status = message.approvalStatus || 'pending';
    const isApproved = status === 'approved';
    const isRejected = status === 'rejected';

    return (
      <div className={cn(
        'rounded-xl border p-3 space-y-1.5 transition-colors',
        isApproved && 'border-accent/30 bg-accent/5',
        isRejected && 'border-destructive/20 bg-destructive/5',
        !isApproved && !isRejected && 'border-primary/20 bg-primary/5',
      )}>
        <div className="flex items-center gap-1.5">
          {isApproved && <CheckCircle2 className="h-3.5 w-3.5 text-accent shrink-0" />}
          {isRejected && <XCircle className="h-3.5 w-3.5 text-destructive shrink-0" />}
          {!isApproved && !isRejected && <Lightbulb className="h-3.5 w-3.5 text-primary shrink-0" />}
          <span className={cn(
            'text-[10px] font-semibold uppercase tracking-wide',
            isApproved && 'text-accent',
            isRejected && 'text-destructive',
            !isApproved && !isRejected && 'text-primary',
          )}>
            {isApproved ? 'Accepted' : isRejected ? 'Dismissed' : 'Suggestion'}
          </span>
          <span className="text-[10px] text-muted-foreground ml-auto">{formatMessageTime(message.timestamp)}</span>
        </div>
        <p className="text-sm text-foreground whitespace-pre-line">{message.content}</p>
        {message.draftReply && (
          <div className="rounded-lg bg-card border p-2.5 text-xs text-muted-foreground whitespace-pre-wrap leading-relaxed">
            {message.draftReply}
          </div>
        )}
        {message.suggestionId && (
          <button
            onClick={() => navigate(`/action-desk?suggestion=${message.suggestionId}`)}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:text-primary/80 bg-primary/10 hover:bg-primary/15 rounded-md px-2.5 py-1 transition-colors"
          >
            <ArrowUpRight className="h-3.5 w-3.5" />
            Open suggestion
          </button>
        )}
      </div>
    );
  }

  // Determine alignment and styling
  const isRightAligned = isManager;
  
  const getSenderIcon = () => {
    if (isAI) return <Zap className="h-3.5 w-3.5" />;
    if (isManager) return <User className="h-4 w-4" />;
    if (senderType === 'vendor') return <Wrench className="h-3.5 w-3.5" />;
    return <User className="h-3.5 w-3.5" />;
  };

  return (
    <div className={cn('flex w-full min-w-0 max-w-full flex-col gap-0.5', isRightAligned ? 'items-end' : 'items-start')}>
      <div className={cn('flex w-full min-w-0 max-w-full gap-2.5', isRightAligned ? 'flex-row-reverse' : 'flex-row')}>
        <div className={cn(
          'flex h-7 w-7 shrink-0 items-center justify-center rounded-full',
          isManager && 'bg-primary text-primary-foreground',
          isAI && 'bg-primary/15 text-primary border border-primary/25',
          isOther && 'bg-muted text-muted-foreground',
        )}>
          {getSenderIcon()}
        </div>
        <div className={cn(
          'max-w-[85%] min-w-0 overflow-hidden rounded-2xl px-4 py-2.5 text-sm break-words',
          isManager && 'bg-primary text-primary-foreground rounded-tr-md',
          isAI && 'bg-primary/10 text-foreground rounded-tl-md border border-primary/15',
          isOther && 'bg-muted/70 text-foreground rounded-tl-md',
        )}>
          {message.senderName && (
            <p className={cn(
              'text-[10px] font-semibold mb-1',
              isManager && 'text-primary-foreground/70',
              isAI && 'text-primary',
              isOther && 'text-muted-foreground',
            )}>
              {message.senderName}
              {isAI && <span className="ml-1 font-normal opacity-70">· auto</span>}
            </p>
          )}
          {isAI ? (
            <div className="prose prose-sm w-full min-w-0 max-w-full break-words overflow-hidden dark:prose-invert [&>p]:mb-1.5 [&>p:last-child]:mb-0">
              <ReactMarkdown
                components={{
                  pre: ({ children }) => (
                    <div className="w-full max-w-full min-w-0 overflow-x-auto overflow-y-hidden">
                      <pre className="m-0 min-w-max rounded-lg border bg-background/80 p-3 text-[11px] leading-relaxed whitespace-pre font-mono">
                        {children}
                      </pre>
                    </div>
                  ),
                  code: ({ inline, children, className, ...props }) =>
                    inline ? (
                      <code
                        {...props}
                        className={cn(
                          'rounded bg-background/80 px-1 py-0.5 text-[0.9em] font-mono break-words',
                          className,
                        )}
                      >
                        {children}
                      </code>
                    ) : (
                      <code {...props} className={cn('font-mono', className)}>
                        {children}
                      </code>
                    ),
                }}
              >
                {renderedContent}
              </ReactMarkdown>
            </div>
          ) : (
            <p className="break-words overflow-hidden">{message.content}</p>
          )}
          {message.attachments && message.attachments.length > 0 && (
            <div className="flex flex-col gap-1.5 mt-2">
              {message.attachments.map(att => (
                <a
                  key={att.documentId}
                  href={`/documents/${att.documentId}`}
                  onClick={(e) => { e.stopPropagation(); }}
                  className={cn(
                    'flex items-center gap-2 rounded-lg px-3 py-2 text-xs transition-colors',
                    isManager
                      ? 'bg-primary-foreground/15 hover:bg-primary-foreground/25 text-primary-foreground'
                      : 'bg-muted/60 hover:bg-muted text-foreground',
                  )}
                >
                  <FileText className="h-4 w-4 shrink-0" />
                  <span className="truncate">{att.filename}</span>
                  <ArrowUpRight className="h-3 w-3 shrink-0 ml-auto opacity-60" />
                </a>
              ))}
            </div>
          )}
        </div>
      </div>
      <span className={cn('text-[10px] text-muted-foreground px-9', isRightAligned && 'pr-9 pl-0')}>
        {formatMessageTime(message.timestamp)}
      </span>
    </div>
  );
}
