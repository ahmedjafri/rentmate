export const CONVERSATION_READ_EVENT = 'rentmate:conversation-read';

export function notifyConversationRead(conversationId: string) {
  window.dispatchEvent(new CustomEvent(CONVERSATION_READ_EVENT, { detail: { conversationId } }));
}

export function getReadConversationId(event: Event): string | null {
  if (!(event instanceof CustomEvent)) return null;
  const conversationId = event.detail?.conversationId;
  return typeof conversationId === 'string' ? conversationId : null;
}
