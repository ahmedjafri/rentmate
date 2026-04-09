import { useState, useEffect, useRef, useCallback } from 'react';
import { graphqlQuery, CONVERSATIONS_QUERY } from '@/data/api';
import { useApp } from '@/context/AppContext';
import type { ConvSummary, TabKey } from '@/components/chat/ConvRow';

export function useConversations(conversationType: TabKey, limit = 50) {
  const { chatPanel } = useApp();
  const [conversations, setConversations] = useState<ConvSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(() => setRefreshKey(k => k + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    graphqlQuery<{ conversations: ConvSummary[] }>(CONVERSATIONS_QUERY, { conversationType, limit })
      .then((data) => {
        if (!cancelled) setConversations(data.conversations ?? []);
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

  const removeConversation = useCallback((uid: string) => {
    setConversations(prev => prev.filter(c => c.uid !== uid));
  }, []);

  return { conversations, loading, refresh, removeConversation };
}
