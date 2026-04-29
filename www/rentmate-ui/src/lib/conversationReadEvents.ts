import { useCallback, useRef } from 'react';

export const CONVERSATION_READ_EVENT = 'rentmate:conversation-read';

export function notifyConversationRead(conversationId: string) {
  window.dispatchEvent(new CustomEvent(CONVERSATION_READ_EVENT, { detail: { conversationId } }));
}

export function getReadConversationId(event: Event): string | null {
  if (!(event instanceof CustomEvent)) return null;
  const conversationId = event.detail?.conversationId;
  return typeof conversationId === 'string' ? conversationId : null;
}

/**
 * Mark a conversation as read on the backend, then dispatch
 * CONVERSATION_READ_EVENT so listeners (per-conversation badge in
 * useConversations + global notification badge in AppContext) update.
 *
 * The event MUST fire after the mutation resolves: both listeners trigger
 * fresh API reads, and dispatching pre-commit races them against stale
 * receipt state — the badges briefly clear, then the in-flight refetch
 * snaps them back to the old count.
 *
 * Returns a deduped marker so each conversation only triggers the
 * mutation once per component lifetime (avoids spam on repeat re-renders).
 */
export function useMarkThreadSeen(
  sendMutation: (uid: string) => Promise<unknown>,
): (conversationId: string) => void {
  const markedRef = useRef<Set<string>>(new Set());
  return useCallback((conversationId: string) => {
    if (markedRef.current.has(conversationId)) return;
    markedRef.current.add(conversationId);
    void sendMutation(conversationId)
      .then(() => notifyConversationRead(conversationId))
      .catch(() => {
        markedRef.current.delete(conversationId);
      });
  }, [sendMutation]);
}
