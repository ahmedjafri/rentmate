import type { ReactNode } from 'react';
import { Bot, MessageCircle, Plus } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { useApp } from '@/context/AppContext';

import { ConvRow, type ConvSummary } from './ConvRow';

/**
 * Reusable left-rail conversation list used by the dashboard and the
 * Chats page. Encapsulates header/title/+ button, loading + empty
 * states, the optional "New Chat" placeholder card the dashboard shows
 * when the user clicks "+", and the row-click → openChat flow.
 *
 * The optional ``header`` slot lets callers mount UI above the list
 * (the Chats page mounts its tenant/vendor type tabs there).
 */
export function ConversationListPane({
  title = 'Chats',
  conversations,
  loading,
  showNewChatPlaceholder = false,
  onNewChat,
  onDelete,
  headerActions,
  header,
}: {
  title?: string;
  conversations: ConvSummary[];
  loading: boolean;
  showNewChatPlaceholder?: boolean;
  onNewChat?: () => void;
  onDelete?: (uid: string) => Promise<void> | void;
  /** Extra controls rendered in the header row alongside title + "+" button. */
  headerActions?: ReactNode;
  header?: ReactNode;
}) {
  const { openChat, chatPanel } = useApp();

  return (
    <>
      <div className="flex items-center justify-between gap-1 px-4 py-3 border-b shrink-0">
        <h2 className="text-sm font-semibold">{title}</h2>
        <div className="flex items-center gap-0.5">
          {headerActions}
          {onNewChat && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              title="New chat"
              onClick={onNewChat}
            >
              <Plus className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>
      {header && <div className="shrink-0">{header}</div>}
      <div className="flex-1 overflow-auto p-2 space-y-1.5">
        {showNewChatPlaceholder && (
          <Card className="px-3 py-2.5 rounded-xl ring-2 ring-primary/40 bg-primary/5">
            <div className="flex items-center gap-1.5">
              <Badge
                variant="secondary"
                className="text-[10px] rounded-lg gap-1 shrink-0 bg-primary/10 text-primary"
              >
                <Bot className="h-3 w-3" />
                RentMate
              </Badge>
            </div>
            <h3 className="font-medium text-sm mt-1.5">New Chat</h3>
          </Card>
        )}
        {loading && (
          <p className="text-xs text-muted-foreground text-center py-4">Loading…</p>
        )}
        {!loading && conversations.length === 0 && !showNewChatPlaceholder && (
          <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
            <MessageCircle className="h-8 w-8 mb-2 opacity-40" />
            <p className="text-xs font-medium">No conversations yet</p>
          </div>
        )}
        {conversations.map((conv) => (
          <ConvRow
            key={conv.uid}
            conv={conv}
            isActive={chatPanel.conversationId === conv.uid}
            onClick={() => openChat({ conversationId: conv.uid })}
            onDelete={
              onDelete
                ? async () => {
                    try {
                      await onDelete(conv.uid);
                      toast.success('Conversation deleted');
                    } catch {
                      toast.error('Failed to delete conversation');
                    }
                  }
                : undefined
            }
          />
        ))}
      </div>
    </>
  );
}
