import { useState } from 'react';
import { useApp } from '@/context/AppContext';
import { Button } from '@/components/ui/button';
import { MessageCircle, Plus } from 'lucide-react';
import { deleteConversation } from '@/graphql/client';
import { toast } from 'sonner';
import { ConvRow, TAB_CONFIG } from '@/components/chat/ConvRow';
import type { TabKey } from '@/components/chat/ConvRow';
import { useConversations } from '@/hooks/useConversations';

const Chats = () => {
  const { openChat, chatPanel } = useApp();
  const [activeTab, setActiveTab] = useState<TabKey>('user_ai');
  const { conversations, loading, refresh, removeConversation } = useConversations(activeTab);

  return (
    <div className="p-4 sm:p-6 max-w-4xl mx-auto space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold">Chats</h1>
        {activeTab === 'user_ai' && (
          <Button size="sm" className="h-8 gap-1.5 text-xs" onClick={() => {
            openChat({ lazy: true });
          }}>
            <Plus className="h-3.5 w-3.5" />
            New Chat
          </Button>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b">
        {TAB_CONFIG.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab.key
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            <tab.icon className="h-3.5 w-3.5" />
            {tab.label}
          </button>
        ))}
      </div>

      {/* Conversation list */}
      <div className="space-y-2">
        {loading && <p className="text-sm text-muted-foreground py-8 text-center">Loading…</p>}
        {!loading && conversations.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
            <MessageCircle className="h-12 w-12 mb-3 opacity-40" />
            <p className="font-medium">No conversations yet</p>
          </div>
        )}
        {conversations.map(conv => (
          <ConvRow
            key={conv.uid}
            conv={conv}
            isActive={chatPanel.isOpen && chatPanel.conversationId === conv.uid}
            onClick={() => openChat({ conversationId: conv.uid })}
            onDelete={async () => {
              try {
                await deleteConversation(conv.uid);
                removeConversation(conv.uid);
                toast.success('Conversation deleted');
              } catch {
                toast.error('Failed to delete conversation');
              }
            }}
          />
        ))}
      </div>
    </div>
  );
};

export default Chats;
