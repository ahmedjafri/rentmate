import { useState, useEffect, useRef } from 'react';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Bot, MessageCircle, Building2, Plus, Trash2 } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { graphqlQuery, DELETE_CONVERSATION_MUTATION } from '@/data/api';
import { toast } from 'sonner';

const CONVERSATIONS_QUERY = `
  query GetConversations($conversationType: String!, $limit: Int) {
    conversations(conversationType: $conversationType, limit: $limit) {
      uid
      conversationType
      title
      lastMessageAt
      updatedAt
      lastMessageBody
      lastMessageSenderName
      propertyName
      participantCount
      unreadCount
    }
  }
`;

interface ConvSummary {
  uid: string;
  conversationType: string;
  title: string | null;
  lastMessageAt: string | null;
  updatedAt: string;
  lastMessageBody: string | null;
  lastMessageSenderName: string | null;
  propertyName: string | null;
  participantCount: number;
  unreadCount: number;
}

type TabKey = 'user_ai' | 'tenant' | 'vendor';

const TAB_CONFIG: { key: TabKey; label: string; icon: React.ElementType }[] = [
  { key: 'user_ai', label: 'With RentMate', icon: Bot },
  { key: 'tenant', label: 'Tenants', icon: MessageCircle },
  { key: 'vendor', label: 'Vendors', icon: Building2 },
];

const typeLabels: Record<string, string> = {
  user_ai: 'RentMate',
  tenant: 'Tenant',
  vendor: 'Vendor',
};

const typeColors: Record<string, string> = {
  user_ai: 'bg-primary/10 text-primary',
  tenant: 'bg-green-800/15 text-green-700 dark:text-green-400',
  vendor: 'bg-orange-100 text-orange-700 dark:bg-orange-900/20 dark:text-orange-400',
};

function ConvRow({ conv, onClick, onDelete, isActive }: { conv: ConvSummary; onClick: () => void; onDelete: () => void; isActive?: boolean }) {
  const TabIcon = TAB_CONFIG.find(t => t.key === conv.conversationType)?.icon ?? MessageCircle;
  const at = conv.lastMessageAt ?? conv.updatedAt;
  const relTime = at ? formatDistanceToNow(new Date(at), { addSuffix: true }) : null;

  return (
    <Card className={`px-3 py-2.5 rounded-xl hover:shadow-md transition-shadow cursor-pointer relative group ${isActive ? 'ring-2 ring-primary/40' : ''}`} onClick={onClick}>
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        className="absolute top-2 right-2 h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors hidden group-hover:flex"
        title="Delete conversation"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>

      <div className="flex items-center justify-between gap-2 pr-6">
        <div className="flex items-center gap-1.5 flex-wrap min-w-0">
          <Badge variant="secondary" className={`text-[10px] rounded-lg gap-1 shrink-0 ${typeColors[conv.conversationType] ?? ''}`}>
            <TabIcon className="h-3 w-3" />
            {typeLabels[conv.conversationType] ?? conv.conversationType}
          </Badge>
          {conv.unreadCount > 0 && (
            <Badge className="h-4 px-1.5 text-[10px] bg-primary text-primary-foreground shrink-0">
              {conv.unreadCount} new
            </Badge>
          )}
        </div>
        {relTime && (
          <span className="text-[10px] text-muted-foreground shrink-0">{relTime}</span>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 mt-1.5">
        <h3 className="font-medium text-sm truncate">{conv.title ?? 'Conversation'}</h3>
        {conv.propertyName && (
          <span className="text-[10px] text-muted-foreground shrink-0">{conv.propertyName}</span>
        )}
      </div>

      {conv.lastMessageBody && (
        <p className="text-xs text-muted-foreground mt-1 truncate">
          {conv.lastMessageSenderName && <span className="font-medium">{conv.lastMessageSenderName}: </span>}
          {conv.lastMessageBody}
        </p>
      )}
    </Card>
  );
}

const Chats = () => {
  const { openChat, chatPanel } = useApp();
  const [activeTab, setActiveTab] = useState<TabKey>('user_ai');
  const [conversations, setConversations] = useState<ConvSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    graphqlQuery<{ conversations: ConvSummary[] }>(CONVERSATIONS_QUERY, { conversationType: activeTab, limit: 50 })
      .then((data) => {
        if (!cancelled) setConversations(data.conversations ?? []);
      })
      .catch(() => { if (!cancelled) setConversations([]); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [activeTab, refreshKey]);

  // Re-fetch when chat panel closes (new conversation may have been created)
  const prevOpen = useRef(chatPanel.isOpen);
  useEffect(() => {
    if (prevOpen.current && !chatPanel.isOpen) {
      setRefreshKey(k => k + 1);
    }
    prevOpen.current = chatPanel.isOpen;
  }, [chatPanel.isOpen]);

  return (
    <div className="p-4 sm:p-6 max-w-4xl mx-auto space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold">Chats</h1>
        {activeTab === 'user_ai' && (
          <Button size="sm" className="h-8 gap-1.5 text-xs" onClick={() => {
            openChat();
            // Refresh list after a short delay to pick up the new conversation
            setTimeout(() => setRefreshKey(k => k + 1), 500);
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
                await graphqlQuery(DELETE_CONVERSATION_MUTATION, { uid: conv.uid });
                setConversations(prev => prev.filter(c => c.uid !== conv.uid));
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
