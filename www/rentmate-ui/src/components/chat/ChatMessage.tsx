import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Bot, User, Eye, ShieldAlert, Check, X, Pencil, ChevronDown, ChevronUp, CheckCircle2, XCircle, Zap, Building2, Wrench, BookOpen, ArrowUpRight } from 'lucide-react';

function ThinkingChain({ steps, isChain }: { steps: string[]; isChain: boolean }) {
  const [expanded, setExpanded] = useState(false);
  if (!isChain) {
    return (
      <div className="flex items-start gap-2 py-1">
        <Eye className="h-3 w-3 text-muted-foreground mt-0.5 shrink-0" />
        <p className="text-[11px] text-muted-foreground italic leading-relaxed">{steps[0]}</p>
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
            <li key={i} className="text-[11px] text-muted-foreground italic leading-relaxed">{step}</li>
          ))}
        </ol>
      )}
    </div>
  );
}
import { ChatMessage as ChatMessageType } from '@/data/mockData';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useApp } from '@/context/AppContext';

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

interface Props {
  message: ChatMessageType;
  onApprove?: (messageId: string) => void;
  onReject?: (messageId: string) => void;
  onEdit?: (messageId: string) => void;
}

export function ChatMessageBubble({ message, onApprove, onReject, onEdit }: Props) {
  const isAssistant = message.role === 'assistant';
  const msgType = message.messageType || 'message';
  const senderType = message.senderType || (isAssistant ? 'ai' : 'manager');
  const [draftExpanded, setDraftExpanded] = useState(false);
  const [detailsExpanded, setDetailsExpanded] = useState(
    !message.approvalStatus || message.approvalStatus === 'pending'
  );

  const isManager = senderType === 'manager';
  const isAI = senderType === 'ai';
  const isOther = senderType === 'tenant' || senderType === 'vendor';

  // Context message — opening summary with optional cross-references
  if (msgType === 'context') {
    return (
      <div className="rounded-xl border border-primary/15 bg-primary/5 p-3 space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <BookOpen className="h-3.5 w-3.5 text-primary" />
            <span className="text-[10px] font-semibold uppercase tracking-wide text-primary">Context</span>
          </div>
          <span className="text-[10px] text-muted-foreground">{formatMessageTime(message.timestamp)}</span>
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

  // Approval requests — highlighted card style with draft + actions
  if (isAssistant && msgType === 'approval') {
    const status = message.approvalStatus || 'pending';
    const isPending = status === 'pending';
    const isApproved = status === 'approved';
    const isRejected = status === 'rejected';

    // Collapsed view for approved/rejected
    if (!isPending && !detailsExpanded) {
      return (
        <div className={cn(
          'rounded-xl border px-3 py-2 transition-colors',
          isApproved && 'border-accent/30 bg-accent/5',
          isRejected && 'border-destructive/20 bg-destructive/5',
        )}>
          <button
            onClick={() => setDetailsExpanded(true)}
            className="flex items-center gap-1.5 w-full"
          >
            {isApproved && <CheckCircle2 className="h-3.5 w-3.5 text-accent shrink-0" />}
            {isRejected && <XCircle className="h-3.5 w-3.5 text-destructive shrink-0" />}
            <span className={cn(
              'text-[10px] font-semibold uppercase tracking-wide',
              isApproved && 'text-accent',
              isRejected && 'text-destructive',
            )}>
              {isApproved ? 'Approved' : 'Rejected'}
            </span>
            <ChevronDown className="h-3 w-3 text-muted-foreground ml-auto" />
          </button>
        </div>
      );
    }

    return (
      <div className={cn(
        'rounded-xl border p-3 space-y-2 transition-colors',
        isPending && 'border-warning/30 bg-warning/5',
        isApproved && 'border-accent/30 bg-accent/5',
        isRejected && 'border-destructive/20 bg-destructive/5',
      )}>
        {/* Header */}
        <div className="flex items-center gap-1.5">
          {isPending && <ShieldAlert className="h-3.5 w-3.5 text-warning-foreground" />}
          {isApproved && <CheckCircle2 className="h-3.5 w-3.5 text-accent" />}
          {isRejected && <XCircle className="h-3.5 w-3.5 text-destructive" />}
          <span className={cn(
            'text-[10px] font-semibold uppercase tracking-wide',
            isPending && 'text-warning-foreground',
            isApproved && 'text-accent',
            isRejected && 'text-destructive',
          )}>
            {isPending ? 'Approval needed' : isApproved ? 'Approved' : 'Rejected'}
          </span>
          {!isPending && (
            <button onClick={() => setDetailsExpanded(false)} className="ml-auto">
              <ChevronUp className="h-3 w-3 text-muted-foreground" />
            </button>
          )}
        </div>

        {/* Summary */}
        <p className="text-sm text-foreground">{message.content}</p>

        {/* Expandable draft reply */}
        {message.draftReply && (
          <div>
            <button
              onClick={() => setDraftExpanded(!draftExpanded)}
              className="flex items-center gap-1 text-[11px] font-medium text-primary hover:text-primary/80 transition-colors"
            >
              {draftExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              {draftExpanded ? 'Hide draft reply' : 'View draft reply'}
            </button>
            {draftExpanded && (
              <div className="mt-2 rounded-lg bg-card border p-3 text-xs text-foreground whitespace-pre-wrap leading-relaxed">
                {message.draftReply}
              </div>
            )}
          </div>
        )}

        {/* Action buttons */}
        {isPending && (
          <div className="flex items-center gap-2 pt-1">
            <Button
              size="sm"
              className="h-7 rounded-lg gap-1.5 text-xs bg-primary hover:bg-primary/90 text-primary-foreground"
              onClick={() => onApprove?.(message.id)}
            >
              <Pencil className="h-3 w-3" />
              Insert
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 rounded-lg gap-1.5 text-xs hover:bg-destructive/10 hover:text-destructive hover:border-destructive/30"
              onClick={() => onReject?.(message.id)}
            >
              <X className="h-3 w-3" />
              Reject
            </Button>
          </div>
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
    <div className={cn('flex flex-col gap-0.5', isRightAligned ? 'items-end' : 'items-start')}>
      <div className={cn('flex gap-2.5 min-w-0 w-full', isRightAligned ? 'flex-row-reverse' : 'flex-row')}>
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
            <div className="prose prose-sm max-w-none break-words dark:prose-invert [&>p]:mb-1.5 [&>p:last-child]:mb-0">
              <ReactMarkdown>{message.content}</ReactMarkdown>
            </div>
          ) : (
            <p className="break-words overflow-hidden">{message.content}</p>
          )}
        </div>
      </div>
      <span className={cn('text-[10px] text-muted-foreground px-9', isRightAligned && 'pr-9 pl-0')}>
        {formatMessageTime(message.timestamp)}
      </span>
    </div>
  );
}