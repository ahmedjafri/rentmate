import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { ChatFilterDropdown, type ChatFilter } from '@/components/chat/ChatFilterDropdown';
import { ChatWorkspaceLayout } from '@/components/chat/ChatWorkspaceLayout';
import { ConversationListPane } from '@/components/chat/ConversationListPane';
import { useApp } from '@/context/AppContext';
import { deleteConversation } from '@/graphql/client';
import { useConversations } from '@/hooks/useConversations';

const Chats = () => {
  const { openChat, suggestions } = useApp();
  const [searchParams] = useSearchParams();
  const [filter, setFilter] = useState<ChatFilter>('all');
  const suggestionId = searchParams.get('suggestion');

  useEffect(() => {
    if (!suggestionId) return;
    const suggestion = suggestions.find(item => item.id === suggestionId);
    if (!suggestion) return;
    if (suggestion.targetConversationId) {
      openChat({ conversationId: suggestion.targetConversationId, suggestionId });
      return;
    }
    openChat({ suggestionId });
  }, [openChat, suggestionId, suggestions]);

  // Always fetch all four buckets so flipping the filter is instant —
  // the cost is one extra round-trip up front and lets us merge for the
  // "All" view without re-querying.
  const ai = useConversations('user_ai');
  const tenants = useConversations('tenant');
  const vendors = useConversations('vendor');
  const tenantcloud = useConversations('mirrored_chat');

  const sources: Record<ChatFilter, ReturnType<typeof useConversations>> = {
    all: ai, // placeholder, overridden below
    user_ai: ai,
    tenant: tenants,
    vendor: vendors,
    mirrored_chat: tenantcloud,
  };

  const conversations = useMemo(() => {
    if (filter === 'all') {
      return [
        ...ai.conversations,
        ...tenants.conversations,
        ...vendors.conversations,
        ...tenantcloud.conversations,
      ].sort(
        (a, b) => {
          const at = a.lastMessageAt ?? a.updatedAt;
          const bt = b.lastMessageAt ?? b.updatedAt;
          return new Date(bt).getTime() - new Date(at).getTime();
        },
      );
    }
    return sources[filter].conversations;
  }, [
    filter,
    ai.conversations,
    tenants.conversations,
    vendors.conversations,
    tenantcloud.conversations,
    sources,
  ]);

  const loading =
    filter === 'all'
      ? ai.loading || tenants.loading || vendors.loading || tenantcloud.loading
      : sources[filter].loading;

  // Removing a conversation needs to drop it from whichever list owns it.
  const removeFromAll = (uid: string) => {
    ai.removeConversation(uid);
    tenants.removeConversation(uid);
    vendors.removeConversation(uid);
    tenantcloud.removeConversation(uid);
  };

  const leftRail = (
    <ConversationListPane
      title="Chats"
      conversations={conversations}
      loading={loading}
      onNewChat={
        // Only the RentMate / All filter view exposes "+", since New Chat
        // means a new RentMate (user_ai) conversation.
        filter === 'all' || filter === 'user_ai'
          ? () => openChat({ lazy: true })
          : undefined
      }
      onDelete={async (uid) => {
        await deleteConversation(uid);
        removeFromAll(uid);
      }}
      headerActions={<ChatFilterDropdown value={filter} onChange={setFilter} />}
    />
  );

  // No rightRail — the embedded ChatPanel fills the rest of the width
  // (this is the "2 columns wide" layout the chats page wants).
  return <ChatWorkspaceLayout leftRail={leftRail} />;
};

export default Chats;
