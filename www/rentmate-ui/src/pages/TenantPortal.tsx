import { useEffect, useMemo, useState } from 'react';

import { ChatMessage, ChatSenderType } from '@/data/mockData';
import { getTenantToken, isTenantAuthenticated, tenantLogout } from '@/lib/tenantAuth';
import { PortalConversationList } from '@/components/portal/PortalConversationList';
import { PortalConversationPanel } from '@/components/portal/PortalConversationPanel';
import { PortalDashboardShell } from '@/components/portal/PortalDashboardShell';
import type { PortalConversationDetail, PortalConversationSummary, PortalTaskMessage } from '@/components/portal/types';
import { Button } from '@/components/ui/button';

interface TenantMe {
  id: string;
  name: string;
  email?: string;
  phone?: string;
}

function authHeaders() {
  return { Authorization: `Bearer ${getTenantToken()}` };
}

function jsonHeaders() {
  return { ...authHeaders(), 'Content-Type': 'application/json' };
}

function tenantMessageToChat(message: PortalTaskMessage): ChatMessage {
  let senderType: ChatSenderType;
  if (message.sender_type === 'tenant') {
    senderType = 'manager';
  } else if (message.is_ai) {
    senderType = 'ai';
  } else {
    senderType = 'tenant';
  }
  return {
    id: message.id,
    role: message.sender_type === 'tenant' ? 'user' : 'assistant',
    content: message.body,
    senderName: message.sender_type === 'tenant' ? 'You' : message.sender_name,
    senderType,
    timestamp: new Date(message.sent_at),
    messageType: 'message',
  };
}

const TenantPortal = () => {
  const [me, setMe] = useState<TenantMe | null>(null);
  const [conversations, setConversations] = useState<PortalConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [activeConversation, setActiveConversation] = useState<PortalConversationDetail | null>(null);
  const [loadingConversationId, setLoadingConversationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeConversationKey = useMemo(() => activeConversationId, [activeConversationId]);

  const loadConversation = async (conversationId: string) => {
    setLoadingConversationId(conversationId);
    try {
      const response = await fetch(`/api/tenant/conversations/${conversationId}`, { headers: authHeaders() });
      if (!response.ok) {
        throw new Error('Failed to load conversation');
      }
      const detail: PortalConversationDetail = await response.json();
      setActiveConversation(detail);
      setActiveConversationId(detail.id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoadingConversationId(null);
    }
  };

  const refreshList = async () => {
    const response = await fetch('/api/tenant/conversations', { headers: authHeaders() });
    if (!response.ok) {
      throw new Error('Failed to load conversations');
    }
    const rows: PortalConversationSummary[] = await response.json();
    setConversations(rows);
    if (!activeConversationId && rows.length > 0) {
      setActiveConversationId(rows[0].id);
    }
    return rows;
  };

  useEffect(() => {
    if (!isTenantAuthenticated()) {
      setError('Your session has expired. Please use your portal link to sign back in.');
      setLoading(false);
      return;
    }

    Promise.all([
      fetch('/api/tenant/me', { headers: authHeaders() }).then((response) => {
        if (!response.ok) throw new Error('Failed to load profile');
        return response.json();
      }),
      refreshList(),
    ])
      .then(([meData]) => {
        setMe(meData);
      })
      .catch((err) => setError((err as Error).message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!activeConversationKey) {
      setActiveConversation(null);
      return;
    }
    loadConversation(activeConversationKey);
  }, [activeConversationKey]);

  useEffect(() => {
    if (!activeConversationId) return undefined;
    const intervalId = window.setInterval(() => {
      loadConversation(activeConversationId);
      refreshList().catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(intervalId);
  }, [activeConversationId]);

  const handleSend = async (body: string) => {
    if (!activeConversationId || sending) return;
    setSending(true);
    try {
      const response = await fetch(`/api/tenant/conversations/${activeConversationId}/messages`, {
        method: 'POST',
        headers: jsonHeaders(),
        body: JSON.stringify({ body }),
      });
      if (!response.ok) throw new Error('Failed to send message');
      await Promise.all([loadConversation(activeConversationId), refreshList()]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSending(false);
    }
  };

  const handleLogout = () => {
    tenantLogout();
    setError('You have been logged out. Use your portal link to sign back in.');
    setMe(null);
    setConversations([]);
    setActiveConversationId(null);
    setActiveConversation(null);
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-muted/30">
        <p className="text-muted-foreground">Loading...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-muted/30 p-4">
        <div className="text-center space-y-4">
          <p className="text-destructive">{error}</p>
          <Button variant="outline" onClick={handleLogout}>Sign out</Button>
        </div>
      </div>
    );
  }

  return (
    <PortalDashboardShell
      title="Tenant Portal"
      subtitle="RentMate"
      dashboardPath="/tenant-portal"
      identity={me?.name ?? 'Tenant'}
      onLogout={handleLogout}
    >
      <div className="flex flex-1 overflow-hidden">
        <PortalConversationList
          title="Your Conversations"
          emptyText="No active conversations."
          conversations={conversations}
          activeConversationId={activeConversationId}
          loadingConversationId={loadingConversationId}
          onSelectConversation={(conversationId) => {
            setActiveConversationId(conversationId);
          }}
        />
        <PortalConversationPanel
          emptyText="Select a conversation to view messages"
          conversation={activeConversation}
          sending={sending}
          onBack={() => setActiveConversationId(null)}
          onSend={handleSend}
          mapMessage={tenantMessageToChat}
        />
      </div>
    </PortalDashboardShell>
  );
};

export default TenantPortal;
