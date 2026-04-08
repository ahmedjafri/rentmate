import { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import { Bot, User, Eye, Lightbulb, Check, X, Send, Pencil, ChevronDown, ChevronUp, CheckCircle2, XCircle, Zap, Building2, Wrench, BookOpen, ArrowUpRight, Loader2, Expand } from 'lucide-react';
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

  // Context message — opening summary with optional cross-references
  if (msgType === 'context') {
    return <ContextBubble message={message} taskId={taskId} />;
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