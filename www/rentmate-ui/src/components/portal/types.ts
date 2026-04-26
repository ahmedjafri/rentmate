import type { ChatMessage } from '@/data/mockData';

export interface PortalLinkedTask {
  id: string;
  task_number?: number;
  title: string;
  status?: string | null;
  category?: string | null;
  urgency?: string | null;
}

export interface PortalConversationSummary {
  id: string;
  conversation_type: string;
  title: string | null;
  updated_at: string;
  last_message_at: string | null;
  last_message_body: string | null;
  last_message_sender_name: string | null;
  typing?: boolean;
  linked_task: PortalLinkedTask | null;
}

export interface PortalTaskMessage {
  id: string;
  body: string;
  sender_name: string;
  sender_type: string;
  is_ai: boolean;
  sent_at: string;
}

export interface PortalConversationDetail extends PortalConversationSummary {
  messages: PortalTaskMessage[];
}

export interface PortalThreadMessageMapper {
  (message: PortalTaskMessage): ChatMessage;
}
