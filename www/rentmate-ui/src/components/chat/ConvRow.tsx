import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Bot, MessageCircle, Building2, Trash2, ClipboardList, Cloud, Lock } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

export interface ConvSummary {
  uid: string;
  conversationType: string;
  title: string | null;
  lastMessageAt: string | null;
  updatedAt: string;
  lastMessageBody: string | null;
  lastMessageSenderName: string | null;
  propertyName: string | null;
  participantCount: number;
  unreadCount: number;
  participantLabel?: string | null;
  taskId?: string | null;
  taskTitle?: string | null;
}

export type TabKey = 'user_ai' | 'tenant' | 'vendor' | 'mirrored_chat';

export const TAB_CONFIG: { key: TabKey; label: string; icon: React.ElementType }[] = [
  { key: 'user_ai', label: 'With RentMate', icon: Bot },
  { key: 'tenant', label: 'Tenants', icon: MessageCircle },
  { key: 'vendor', label: 'Vendors', icon: Building2 },
  { key: 'mirrored_chat', label: 'Mirrored', icon: Cloud },
];

export const typeLabels: Record<string, string> = {
  user_ai: 'RentMate',
  tenant: 'Tenant',
  vendor: 'Vendor',
  mirrored_chat: 'Mirrored',
};

export const typeColors: Record<string, string> = {
  user_ai: 'bg-primary/10 text-primary',
  tenant: 'bg-green-800/15 text-green-700 dark:text-green-400',
  vendor: 'bg-orange-100 text-orange-700 dark:bg-orange-900/20 dark:text-orange-400',
  mirrored_chat: 'bg-sky-100 text-sky-700 dark:bg-sky-900/20 dark:text-sky-400',
};

export function ConvRow({ conv, onClick, onDelete, isActive }: { conv: ConvSummary; onClick: () => void; onDelete?: () => void; isActive?: boolean }) {
  const TabIcon = TAB_CONFIG.find(t => t.key === conv.conversationType)?.icon ?? MessageCircle;
  const at = conv.lastMessageAt ?? conv.updatedAt;
  const relTime = at ? formatDistanceToNow(new Date(at), { addSuffix: true }) : null;

  // For tenant/vendor threads the human contact's name is the canonical
  // title; the stored ``subject`` varies (e.g. "Chat with X" vs
  // "Message X: <task>") and is unreliable for display.
  const isExternalConv = conv.conversationType === 'tenant' || conv.conversationType === 'vendor';
  const isMirror = conv.conversationType === 'mirrored_chat';
  const displayTitle = isExternalConv
    ? (conv.participantLabel ?? conv.title)
    : conv.title;

  return (
    <Card className={`px-3 py-2.5 rounded-xl hover:shadow-md transition-shadow cursor-pointer relative group ${isActive ? 'ring-2 ring-primary/40 bg-primary/5' : ''}`} onClick={onClick}>
      {onDelete && (
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          className="absolute top-2 right-2 h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors hidden group-hover:flex"
          title="Delete conversation"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      )}

      <div className="flex items-center justify-between gap-2 pr-6">
        <div className="flex items-center gap-1.5 flex-wrap min-w-0">
          <Badge variant="secondary" className={`text-[10px] rounded-lg gap-1 shrink-0 ${typeColors[conv.conversationType] ?? ''}`}>
            <TabIcon className="h-3 w-3" />
            {typeLabels[conv.conversationType] ?? conv.conversationType}
          </Badge>
          {conv.taskId && (
            <Badge
              variant="secondary"
              className="text-[10px] rounded-lg gap-1 shrink-0 bg-muted text-muted-foreground max-w-[160px]"
              title={conv.taskTitle ? `Task #${conv.taskId} — ${conv.taskTitle}` : `Task #${conv.taskId}`}
            >
              <ClipboardList className="h-3 w-3 shrink-0" />
              <span className="truncate">Task #{conv.taskId}</span>
            </Badge>
          )}
          {conv.unreadCount > 0 && (
            <Badge className="h-4 px-1.5 text-[10px] bg-primary text-primary-foreground shrink-0">
              {conv.unreadCount} new
            </Badge>
          )}
          {isMirror && (
            <Badge
              variant="secondary"
              className="text-[10px] rounded-lg gap-1 shrink-0 bg-muted text-muted-foreground"
              title="Mirrored from another platform — replies must happen there"
            >
              <Lock className="h-3 w-3" />
              Read-only
            </Badge>
          )}
        </div>
        {relTime && (
          <span className="text-[10px] text-muted-foreground shrink-0">{relTime}</span>
        )}
      </div>

      {displayTitle && displayTitle !== 'Chat with RentMate' ? (
        <>
          <h3 className="font-medium text-sm truncate mt-1.5">{displayTitle}</h3>
          {conv.lastMessageBody && (
            <p className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">
              {conv.lastMessageSenderName && <span className="font-medium">{conv.lastMessageSenderName}: </span>}
              {conv.lastMessageBody}
            </p>
          )}
        </>
      ) : (
        <p className="text-[11px] text-muted-foreground mt-1.5 line-clamp-2">
          {conv.lastMessageBody ?? 'Conversation'}
        </p>
      )}
    </Card>
  );
}
