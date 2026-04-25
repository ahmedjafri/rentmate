import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Wrench } from 'lucide-react';

import { ChatMessage, ChatSenderType } from '@/data/mockData';
import { getVendorToken, isVendorAuthenticated, setVendorToken, vendorLogout } from '@/lib/vendorAuth';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { PortalConversationList } from '@/components/portal/PortalConversationList';
import { PortalConversationPanel } from '@/components/portal/PortalConversationPanel';
import { PortalDashboardShell } from '@/components/portal/PortalDashboardShell';
import type { PortalConversationDetail, PortalConversationSummary, PortalTaskMessage } from '@/components/portal/types';

interface VendorMe {
  id: string;
  name: string;
  company?: string;
  vendor_type?: string;
  email?: string;
  has_account?: boolean;
}

function authHeaders() {
  return { Authorization: `Bearer ${getVendorToken()}` };
}

function jsonHeaders() {
  return { ...authHeaders(), 'Content-Type': 'application/json' };
}

function vendorMessageToChat(message: PortalTaskMessage): ChatMessage {
  let senderType: ChatSenderType;
  if (message.sender_type === 'external_contact') {
    senderType = 'manager';
  } else if (message.is_ai) {
    senderType = 'ai';
  } else {
    senderType = 'tenant';
  }
  return {
    id: message.id,
    role: message.sender_type === 'external_contact' ? 'user' : 'assistant',
    content: message.body,
    senderName: message.sender_type === 'external_contact' ? 'You' : message.sender_name,
    senderType,
    timestamp: new Date(message.sent_at),
    messageType: 'message',
  };
}

function VendorAuthCard({
  mode,
  initialEmail,
  portalToken,
  onLogin,
}: {
  mode: 'login' | 'signup';
  initialEmail?: string;
  portalToken?: string;
  onLogin: (token: string) => void;
}) {
  const [email, setEmail] = useState(initialEmail ?? '');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const response = await fetch('/api/vendor/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, token: portalToken || null }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.access_token) {
        throw new Error(data.detail || 'Unable to sign in');
      }
      onLogin(data.access_token);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-muted/30 p-4">
      <Card className="w-full max-w-md p-8 space-y-6">
        <div className="text-center space-y-2">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10">
            <Wrench className="h-6 w-6 text-primary" />
          </div>
          <h1 className="text-xl font-semibold">Vendor Portal</h1>
          <p className="text-sm text-muted-foreground">
            {mode === 'signup'
              ? 'Finish creating your vendor account to keep using the portal.'
              : portalToken
                ? 'Sign in to link your existing account and access the vendor portal.'
                : 'Sign in to access your assigned tasks and conversations.'}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="vendor-email">Email</Label>
            <Input
              id="vendor-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="vendor@example.com"
              autoFocus
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="vendor-password">Password</Label>
            <Input
              id="vendor-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter your password"
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? 'Signing in...' : 'Sign in'}
          </Button>
        </form>
      </Card>
    </div>
  );
}

const VendorPortal = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const [me, setMe] = useState<VendorMe | null>(null);
  const [conversations, setConversations] = useState<PortalConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [activeConversation, setActiveConversation] = useState<PortalConversationDetail | null>(null);
  const [loadingConversationId, setLoadingConversationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [authVersion, setAuthVersion] = useState(0);
  const [accountEmail, setAccountEmail] = useState(searchParams.get('email') || '');
  const [accountPassword, setAccountPassword] = useState('');
  const [accountError, setAccountError] = useState<string | null>(null);
  const [accountSaving, setAccountSaving] = useState(false);
  const initialMode = searchParams.get('mode') === 'login' ? 'login' : 'signup';
  const portalToken = searchParams.get('token') || '';

  const activeConversationKey = useMemo(() => activeConversationId, [activeConversationId]);

  const refreshList = async () => {
    const response = await fetch('/api/vendor/conversations', { headers: authHeaders() });
    if (!response.ok) throw new Error('Failed to load conversations');
    const rows: PortalConversationSummary[] = await response.json();
    setConversations(rows);
    if (!activeConversationId && rows.length > 0) {
      setActiveConversationId(rows[0].id);
    }
    return rows;
  };

  const loadConversation = async (conversationId: string) => {
    setLoadingConversationId(conversationId);
    try {
      const response = await fetch(`/api/vendor/conversations/${conversationId}`, { headers: authHeaders() });
      if (!response.ok) throw new Error('Failed to load conversation');
      const detail: PortalConversationDetail = await response.json();
      setActiveConversation(detail);
      setActiveConversationId(detail.id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoadingConversationId(null);
    }
  };

  useEffect(() => {
    if (!isVendorAuthenticated()) {
      setLoading(false);
      return;
    }

    Promise.all([
      fetch('/api/vendor/me', { headers: authHeaders() }).then((response) => {
        if (!response.ok) throw new Error('Failed to load profile');
        return response.json();
      }),
      refreshList(),
    ])
      .then(([meData]) => {
        setMe(meData);
        if (meData.email) setAccountEmail((prev) => prev || meData.email);
      })
      .catch((err) => setError((err as Error).message))
      .finally(() => setLoading(false));
  }, [authVersion]);

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

  const handleAccountCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setAccountError(null);
    setAccountSaving(true);
    try {
      const response = await fetch('/api/vendor/account', {
        method: 'POST',
        headers: jsonHeaders(),
        body: JSON.stringify({ email: accountEmail, password: accountPassword }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.access_token) {
        throw new Error(data.detail || 'Unable to create account');
      }
      setVendorToken(data.access_token);
      setMe((prev) => (prev ? { ...prev, email: data.email, has_account: true } : prev));
      setAccountPassword('');
      const next = new URLSearchParams(searchParams);
      next.delete('token');
      next.delete('mode');
      next.delete('email');
      setSearchParams(next, { replace: true });
    } catch (err) {
      setAccountError((err as Error).message);
    } finally {
      setAccountSaving(false);
    }
  };

  const handleVendorLogin = (token: string) => {
    setVendorToken(token);
    setError(null);
    setLoading(true);
    setAuthVersion((value) => value + 1);
  };

  const handleLogout = () => {
    vendorLogout();
    setError(null);
    setMe(null);
    setConversations([]);
    setActiveConversationId(null);
    setActiveConversation(null);
    setAuthVersion((value) => value + 1);
  };

  const handleSend = async (body: string) => {
    if (!activeConversationId || sending) return;
    setSending(true);
    try {
      const response = await fetch(`/api/vendor/conversations/${activeConversationId}/messages`, {
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

  if (!isVendorAuthenticated()) {
    return (
      <VendorAuthCard
        mode={initialMode}
        initialEmail={searchParams.get('email') || undefined}
        portalToken={portalToken || undefined}
        onLogin={handleVendorLogin}
      />
    );
  }

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

  const accountNotice = me && !me.has_account ? (
    <div className="border-b p-4 bg-muted/20">
      <p className="mb-3 text-xs text-muted-foreground">Create a login to keep using the portal without an invite link.</p>
      <form onSubmit={handleAccountCreate} className="space-y-3">
        <div className="space-y-1">
          <Label htmlFor="vendor-account-email" className="text-xs">Email</Label>
          <Input
            id="vendor-account-email"
            type="email"
            value={accountEmail}
            onChange={(e) => setAccountEmail(e.target.value)}
            placeholder="vendor@example.com"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="vendor-account-password" className="text-xs">Password</Label>
          <Input
            id="vendor-account-password"
            type="password"
            value={accountPassword}
            onChange={(e) => setAccountPassword(e.target.value)}
            placeholder="Create a password"
          />
        </div>
        {accountError && <p className="text-xs text-destructive">{accountError}</p>}
        <div className="flex gap-2">
          <Button type="submit" size="sm" className="flex-1" disabled={accountSaving}>
            {accountSaving ? 'Creating account...' : 'Create account'}
          </Button>
          {portalToken && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="flex-1"
              onClick={() => {
                vendorLogout();
                const next = new URLSearchParams(searchParams);
                next.set('mode', 'login');
                next.set('token', portalToken);
                if (accountEmail) next.set('email', accountEmail);
                setSearchParams(next, { replace: true });
                setMe(null);
                setConversations([]);
                setActiveConversationId(null);
                setActiveConversation(null);
              }}
            >
              Sign in instead
            </Button>
          )}
        </div>
      </form>
    </div>
  ) : undefined;

  return (
    <PortalDashboardShell
      title="Vendor Portal"
      subtitle="RentMate"
      dashboardPath="/vendor-portal"
      identity={me?.company ? `${me.name} · ${me.company}` : (me?.name ?? 'Vendor')}
      onLogout={handleLogout}
    >
      <div className="flex flex-1 overflow-hidden">
        <PortalConversationList
          title="Assigned Conversations"
          emptyText="No conversations assigned yet."
          accountNotice={accountNotice}
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
          mapMessage={vendorMessageToChat}
        />
      </div>
    </PortalDashboardShell>
  );
};

export default VendorPortal;
