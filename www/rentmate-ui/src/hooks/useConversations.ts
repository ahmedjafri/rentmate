import { useState, useEffect, useRef, useCallback } from 'react';
import { useApp } from '@/context/AppContext';
import type { ConvSummary, TabKey } from '@/components/chat/ConvRow';
import { fromGraphqlEnum, getConversations } from '@/graphql/client';
import { CONVERSATION_READ_EVENT, getReadConversationId } from '@/lib/conversationReadEvents';

export function useConversations(conversationType: TabKey, limit = 50) {
  const { chatPanel } = useApp();
  const [conversations, setConversations] = useState<ConvSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(() => setRefreshKey(k => k + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    getConversations(conversationType, limit)
      .then((data) => {
        if (!cancelled) {
          setConversations((data.conversations ?? []).map((conversation) => ({
            ...conversation,
            conversationType: fromGraphqlEnum(conversation.conversationType) ?? conversation.conversationType,
          })) as ConvSummary[]);
        }
      })
      .catch(() => { if (!cancelled) setConversations([]); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [conversationType, limit, refreshKey]);

  // Re-fetch when chat panel closes (new conversation may have been created)
  const prevOpen = useRef(chatPanel.isOpen);
  useEffect(() => {
    if (prevOpen.current && !chatPanel.isOpen) {
      refresh();
    }
    prevOpen.current = chatPanel.isOpen;
  }, [chatPanel.isOpen, refresh]);

  // Re-fetch when a lazy chat gets its backend conversation ID (first message sent)
  const prevConvId = useRef(chatPanel.conversationId);
  useEffect(() => {
    if (!prevConvId.current && chatPanel.conversationId) {
      refresh();
    }
    prevConvId.current = chatPanel.conversationId;
  }, [chatPanel.conversationId, refresh]);

  useEffect(() => {
    const onConversationRead = (event: Event) => {
      const conversationId = getReadConversationId(event);
      if (!conversationId) return;
      setConversations(prev => prev.map(conv => (
        conv.uid === conversationId ? { ...conv, unreadCount: 0 } : conv
      )));
    };
    window.addEventListener(CONVERSATION_READ_EVENT, onConversationRead);
    return () => window.removeEventListener(CONVERSATION_READ_EVENT, onConversationRead);
  }, []);

  const removeConversation = useCallback((uid: string) => {
    setConversations(prev => prev.filter(c => c.uid !== uid));
  }, []);

  return { conversations, loading, refresh, removeConversation };
}
