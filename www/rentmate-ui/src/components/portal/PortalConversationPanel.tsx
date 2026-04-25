import { useEffect, useRef } from 'react';
import { ArrowLeft } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { ChatInput } from '@/components/chat/ChatInput';
import { ChatMessageBubble } from '@/components/chat/ChatMessage';

import type { PortalConversationDetail, PortalThreadMessageMapper } from './types';

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-blue-100 text-blue-800',
  suggested: 'bg-yellow-100 text-yellow-800',
  resolved: 'bg-green-100 text-green-800',
  cancelled: 'bg-gray-100 text-gray-700',
  paused: 'bg-orange-100 text-orange-800',
};

export function PortalConversationPanel({
  emptyText,
  conversation,
  sending,
  onBack,
  onSend,
  mapMessage,
}: {
  emptyText: string;
  conversation: PortalConversationDetail | null;
  sending: boolean;
  onBack: () => void;
  onSend: (body: string) => Promise<void> | void;
  mapMessage: PortalThreadMessageMapper;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [conversation?.messages, conversation?.typing]);

  return (
    <div className={`${conversation ? 'flex' : 'hidden md:flex'} flex-col flex-1 overflow-hidden`}>
      {!conversation ? (
        <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
          {emptyText}
        </div>
      ) : (
        <>
          <div className="flex items-center gap-3 px-4 py-3 border-b bg-background shrink-0">
            <button onClick={onBack} className="md:hidden p-1 rounded hover:bg-muted" aria-label="Back to conversations">
              <ArrowLeft className="h-4 w-4" />
            </button>
            <div className="flex-1 min-w-0">
              <p className="font-semibold text-sm leading-tight truncate">{conversation.title ?? 'Conversation'}</p>
              {conversation.linked_task && (
                <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
                  <span className="text-xs text-muted-foreground">
                    #{conversation.linked_task.task_number ?? conversation.linked_task.id}
                  </span>
                  {conversation.linked_task.category && (
                    <span className="text-xs text-muted-foreground capitalize">{conversation.linked_task.category}</span>
                  )}
                  {conversation.linked_task.status && (
                    <Badge className={`text-xs h-4 px-1.5 ${STATUS_COLORS[conversation.linked_task.status] ?? 'bg-gray-100 text-gray-700'}`}>
                      {conversation.linked_task.status}
                    </Badge>
                  )}
                </div>
              )}
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
            {conversation.messages.length === 0 && (
              <p className="text-sm text-muted-foreground text-center pt-8">No messages yet.</p>
            )}
            {conversation.messages.map((message) => (
              <ChatMessageBubble key={message.id} message={mapMessage(message)} />
            ))}
            <div ref={bottomRef} />
          </div>

          <ChatInput onSend={onSend} disabled={sending} />
        </>
      )}
    </div>
  );
}
