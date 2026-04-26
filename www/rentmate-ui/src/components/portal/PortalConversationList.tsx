import type { ReactNode } from 'react';

import { Badge } from '@/components/ui/badge';
import { ConvRow, type ConvSummary } from '@/components/chat/ConvRow';

import type { PortalConversationSummary } from './types';

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-blue-100 text-blue-800',
  suggested: 'bg-yellow-100 text-yellow-800',
  resolved: 'bg-green-100 text-green-800',
  cancelled: 'bg-gray-100 text-gray-700',
  paused: 'bg-orange-100 text-orange-800',
};

function toConvSummary(conversation: PortalConversationSummary): ConvSummary {
  return {
    uid: conversation.id,
    conversationType: conversation.conversation_type as ConvSummary['conversationType'],
    title: conversation.title,
    lastMessageAt: conversation.last_message_at,
    updatedAt: conversation.updated_at,
    lastMessageBody: conversation.last_message_body,
    lastMessageSenderName: conversation.last_message_sender_name,
    propertyName: null,
    participantCount: 1,
    unreadCount: 0,
  };
}

export function PortalConversationList({
  title,
  emptyText,
  accountNotice,
  conversations,
  activeConversationId,
  loadingConversationId,
  onSelectConversation,
}: {
  title: string;
  emptyText: string;
  accountNotice?: ReactNode;
  conversations: PortalConversationSummary[];
  activeConversationId: string | null;
  loadingConversationId: string | null;
  onSelectConversation: (conversationId: string) => void;
}) {
  return (
    <div className={`${activeConversationId ? 'hidden md:flex' : 'flex'} flex-col w-full md:w-80 border-r bg-background shrink-0`}>
      <div className="px-4 py-3 border-b">
        <h2 className="font-semibold text-sm">{title}</h2>
      </div>
      {accountNotice}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {conversations.length === 0 ? (
          <p className="text-sm text-muted-foreground p-1">{emptyText}</p>
        ) : (
          conversations.map((conversation) => (
            <div key={conversation.id} className={loadingConversationId === conversation.id ? 'opacity-60' : ''}>
              <ConvRow
                conv={toConvSummary(conversation)}
                isActive={activeConversationId === conversation.id}
                onClick={() => onSelectConversation(conversation.id)}
              />
              {conversation.linked_task && (
                <div className="px-2 pt-1.5 flex items-center gap-1.5 flex-wrap">
                  <span className="text-[11px] text-muted-foreground">
                    #{conversation.linked_task.task_number ?? conversation.linked_task.id} {conversation.linked_task.title}
                  </span>
                  {conversation.linked_task.status && (
                    <Badge className={`text-[10px] h-4 px-1.5 ${STATUS_COLORS[conversation.linked_task.status] ?? 'bg-gray-100 text-gray-700'}`}>
                      {conversation.linked_task.status}
                    </Badge>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
