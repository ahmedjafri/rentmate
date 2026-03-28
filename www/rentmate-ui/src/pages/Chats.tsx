import { useState, useEffect } from 'react';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Bot, MessageCircle, Building2, Plus } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { graphqlQuery } from '@/data/api';

const CONVERSATIONS_QUERY = `
  query GetConversations($conversationType: String!, $limit: Int) {
    conversations(conversationType: $conversationType, limit: $limit) {
      uid
      conversationType
      title
      lastMessageAt
      updatedAt
    }
  }
`;

interface ConvSummary {
  uid: string;
  conversationType: string;
  title: string | null;
  lastMessageAt: string | null;
  updatedAt: string;
}

type TabKey = 'user_ai' | 'tenant' | 'vendor';

const TAB_CONFIG: { key: TabKey; label: string; icon: React.ElementType }[] = [
  { key: 'user_ai', label: 'With RentMate', icon: Bot },
  { key: 'tenant', label: 'Tenants', icon: MessageCircle },
  { key: 'vendor', label: 'Vendors', icon: Building2 },
];

function ConvRow({ conv, onClick }: { conv: ConvSummary; onClick: () => void }) {
  const TabIcon = TAB_CONFIG.find(t => t.key === conv.conversationType)?.icon ?? MessageCircle;
  const at = conv.lastMessageAt ?? conv.updatedAt;
  const relTime = at ? formatDistanceToNow(new Date(at), { addSuffix: true }) : null;

  return (
    <Card className="p-4 rounded-xl hover:shadow-md transition-shadow cursor-pointer" onClick={onClick}>
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10">
          <TabIcon className="h-4 w-4 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <p className="text-sm font-semibold truncate">{conv.title ?? 'Conversation'}</p>
            {relTime && (
              <span className="text-[10px] text-muted-foreground shrink-0 mt-0.5">{relTime}</span>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}

const Chats = () => {
  const { openChat } = useApp();
  const [activeTab, setActiveTab] = useState<TabKey>('user_ai');
  const [conversations, setConversations] = useState<ConvSummary[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setConversations([]);

    if (activeTab === 'user_ai') {
      fetch('/chat/conversations', { headers: { 'Content-Type': 'application/json' } })
        .then(r => r.json())
        .then((data: Array<{ id: string; title: string; updated_at: string | null; last_message: string | null }>) => {
          if (!cancelled) {
            setConversations(data.map(d => ({
              uid: d.id,
              conversationType: 'user_ai',
              title: d.title,
              lastMessageAt: d.updated_at,
              updatedAt: d.updated_at ?? '',
            })));
          }
        })
        .catch(() => { if (!cancelled) setConversations([]); })
        .finally(() => { if (!cancelled) setLoading(false); });
    } else {
      graphqlQuery<{ conversations: ConvSummary[] }>(CONVERSATIONS_QUERY, { conversationType: activeTab, limit: 50 })
        .then((data) => {
          if (!cancelled) setConversations(data.conversations ?? []);
        })
        .catch(() => { if (!cancelled) setConversations([]); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }

    return () => { cancelled = true; };
  }, [activeTab]);

  return (
    <div className="p-4 sm:p-6 max-w-4xl mx-auto space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold">Chats</h1>
        {activeTab === 'user_ai' && (
          <Button size="sm" className="h-8 gap-1.5 text-xs" onClick={() => openChat()}>
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
            onClick={() => openChat({ conversationId: conv.uid, conversationType: conv.conversationType as TabKey })}
          />
        ))}
      </div>
    </div>
  );
};

export default Chats;
