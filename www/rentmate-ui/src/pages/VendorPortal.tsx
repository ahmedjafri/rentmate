import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Wrench, LogOut, ArrowLeft } from 'lucide-react';
import { ChatMessageBubble } from '@/components/chat/ChatMessage';
import { ChatInput } from '@/components/chat/ChatInput';
import { ChatMessage, ChatSenderType } from '@/data/mockData';
import { getVendorToken, isVendorAuthenticated, setVendorToken, vendorLogout } from '@/lib/vendorAuth';

// ─── types ────────────────────────────────────────────────────────────────────

interface VendorMe {
  id: string;
  name: string;
  company?: string;
  vendor_type?: string;
  email?: string;
  has_account?: boolean;
}

interface VendorTask {
  id: string;
  task_number?: number;
  title: string;
  status?: string;
  category?: string;
  created_at: string;
}

interface TaskMessage {
  id: string;
  body: string;
  sender_name: string;
  sender_type: string;
  is_ai: boolean;
  sent_at: string;
}

interface TaskDetail extends VendorTask {
  urgency?: string;
  messages: TaskMessage[];
  typing?: boolean;
}

// ─── helpers ──────────────────────────────────────────────────────────────────

function authHeaders() {
  return { Authorization: `Bearer ${getVendorToken()}` };
}

function jsonHeaders() {
  return { ...authHeaders(), 'Content-Type': 'application/json' };
}

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-blue-100 text-blue-800',
  suggested: 'bg-yellow-100 text-yellow-800',
  resolved: 'bg-green-100 text-green-800',
  cancelled: 'bg-gray-100 text-gray-700',
  paused: 'bg-orange-100 text-orange-800',
};

function taskMessageToChat(m: TaskMessage): ChatMessage {
  let senderType: ChatSenderType;
  if (m.sender_type === 'external_contact') {
    senderType = 'manager'; // right-aligned, primary — vendor's own messages
  } else if (m.is_ai) {
    senderType = 'ai';
  } else {
    senderType = 'tenant'; // left-aligned muted — property manager messages
  }
  return {
    id: m.id,
    role: m.sender_type === 'external_contact' ? 'user' : 'assistant',
    content: m.body,
    senderName: m.sender_type === 'external_contact' ? 'You' : m.sender_name,
    senderType,
    timestamp: new Date(m.sent_at),
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

// ─── chat panel ───────────────────────────────────────────────────────────────

function ChatPanel({ task, onBack }: { task: TaskDetail; onBack: () => void }) {
  const [messages, setMessages] = useState<TaskMessage[]>(task.messages);
  const [sending, setSending] = useState(false);
  const [typing, setTyping] = useState(task.typing ?? false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Scroll to bottom when messages change or typing indicator appears
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, typing]);

  // Poll for new messages every 5s
  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const r = await fetch(`/api/vendor/tasks/${task.id}`, { headers: authHeaders() });
        if (r.ok) {
          const data: TaskDetail = await r.json();
          setMessages(data.messages);
          setTyping(data.typing ?? false);
        }
      } catch { /* silent */ }
    }, 5000);
    return () => clearInterval(id);
  }, [task.id]);

  const handleSend = async (text: string) => {
    if (sending) return;
    setSending(true);
    try {
      const r = await fetch(`/api/vendor/tasks/${task.id}/messages`, {
        method: 'POST',
        headers: jsonHeaders(),
        body: JSON.stringify({ body: text }),
      });
      if (!r.ok) throw new Error();
      const msg: TaskMessage = await r.json();
      setMessages(prev => [...prev, msg]);
    } catch { /* ignore */ } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Chat header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b bg-background shrink-0">
        <button onClick={onBack} className="md:hidden p-1 rounded hover:bg-muted">
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-sm leading-tight truncate">
            {task.task_number != null && <span className="text-muted-foreground font-normal">#{task.task_number} </span>}
            {task.title}
          </p>
          <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
            {task.category && (
              <span className="text-xs text-muted-foreground capitalize">{task.category}</span>
            )}
            {task.status && (
              <Badge className={`text-xs h-4 px-1.5 ${STATUS_COLORS[task.status] ?? 'bg-gray-100 text-gray-700'}`}>
                {task.status}
              </Badge>
            )}
          </div>
        </div>
      </div>

      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {messages.length === 0 && (
          <p className="text-sm text-muted-foreground text-center pt-8">No messages yet.</p>
        )}
        {messages.map((m) => (
          <ChatMessageBubble key={m.id} message={taskMessageToChat(m)} />
        ))}
        {typing && (
          <div className="flex items-start gap-2 text-muted-foreground">
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-muted shrink-0 mt-0.5">
              <Wrench className="h-3.5 w-3.5" />
            </div>
            <div className="py-2 px-3 rounded-2xl bg-muted">
              <div className="flex gap-1 py-0.5">
                <span className="w-1.5 h-1.5 bg-muted-foreground/50 rounded-full animate-bounce [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 bg-muted-foreground/50 rounded-full animate-bounce [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 bg-muted-foreground/50 rounded-full animate-bounce [animation-delay:300ms]" />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <ChatInput onSend={handleSend} disabled={sending} />
    </div>
  );
}

// ─── main component ───────────────────────────────────────────────────────────

const VendorPortal = () => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [me, setMe] = useState<VendorMe | null>(null);
  const [tasks, setTasks] = useState<VendorTask[]>([]);
  const [activeTask, setActiveTask] = useState<TaskDetail | null>(null);
  const [loadingTask, setLoadingTask] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [authVersion, setAuthVersion] = useState(0);
  const [accountEmail, setAccountEmail] = useState(searchParams.get('email') || '');
  const [accountPassword, setAccountPassword] = useState('');
  const [accountError, setAccountError] = useState<string | null>(null);
  const [accountSaving, setAccountSaving] = useState(false);
  const initialMode = searchParams.get('mode') === 'login' ? 'login' : 'signup';
  const portalToken = searchParams.get('token') || '';

  useEffect(() => {
    if (!isVendorAuthenticated()) {
      setLoading(false);
      return;
    }

    Promise.all([
      fetch('/api/vendor/me', { headers: authHeaders() }).then((r) => {
        if (!r.ok) throw new Error('Failed to load profile');
        return r.json();
      }),
      fetch('/api/vendor/tasks', { headers: authHeaders() }).then((r) => {
        if (!r.ok) throw new Error('Failed to load tasks');
        return r.json();
      }),
    ])
      .then(([meData, tasksData]) => {
        setMe(meData);
        setTasks(tasksData);
        if (meData.email) setAccountEmail(prev => prev || meData.email);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [authVersion, navigate]);

  const handleAccountCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setAccountError(null);
    setAccountSaving(true);
    try {
      const r = await fetch('/api/vendor/account', {
        method: 'POST',
        headers: jsonHeaders(),
        body: JSON.stringify({ email: accountEmail, password: accountPassword }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.access_token) {
        throw new Error(data.detail || 'Unable to create account');
      }
      setVendorToken(data.access_token);
      setMe(prev => prev ? { ...prev, email: data.email, has_account: true } : prev);
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

  const openTask = async (taskId: string) => {
    setLoadingTask(taskId);
    try {
      const r = await fetch(`/api/vendor/tasks/${taskId}`, { headers: authHeaders() });
      if (!r.ok) throw new Error('Failed to load task');
      setActiveTask(await r.json());
    } catch { /* ignore */ } finally {
      setLoadingTask(null);
    }
  };

  const handleLogout = () => {
    vendorLogout();
    setError(null);
    setMe(null);
    setTasks([]);
    setActiveTask(null);
    setAuthVersion((value) => value + 1);
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

  return (
    <div className="min-h-screen flex flex-col bg-muted/30">
      {/* Top bar */}
      <header className="bg-background border-b px-4 py-3 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="bg-primary/10 rounded-full p-1.5">
            <Wrench className="h-4 w-4 text-primary" />
          </div>
          <div>
            <span className="font-semibold text-sm">{me?.name}</span>
            {me?.company && (
              <span className="text-xs text-muted-foreground ml-1.5">· {me.company}</span>
            )}
          </div>
        </div>
        <Button variant="ghost" size="sm" onClick={handleLogout} className="gap-1.5 text-muted-foreground">
          <LogOut className="h-3.5 w-3.5" />
          Sign out
        </Button>
      </header>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Task list — hidden on mobile when a task is open */}
        <div className={`${activeTask ? 'hidden md:flex' : 'flex'} flex-col w-full md:w-72 lg:w-80 border-r bg-background shrink-0`}>
          <div className="px-4 py-3 border-b">
            <h2 className="font-semibold text-sm">Assigned Tasks</h2>
            {me && !me.has_account && (
              <p className="mt-1 text-xs text-muted-foreground">Create a login to keep using the portal without an invite link.</p>
            )}
          </div>
          {me && !me.has_account && (
            <div className="border-b p-4 bg-muted/20">
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
                        setTasks([]);
                        setActiveTask(null);
                      }}
                    >
                      Sign in instead
                    </Button>
                  )}
                </div>
              </form>
            </div>
          )}
          <div className="flex-1 overflow-y-auto">
            {tasks.length === 0 ? (
              <p className="text-sm text-muted-foreground p-4">No tasks assigned yet.</p>
            ) : (
              tasks.map((t) => {
                const isActive = activeTask?.id === t.id;
                return (
                  <button
                    key={t.id}
                    onClick={() => openTask(t.id)}
                    disabled={loadingTask === t.id}
                    className={`w-full text-left px-4 py-3 border-b transition-colors hover:bg-muted/50 ${
                      isActive ? 'bg-muted' : ''
                    } ${loadingTask === t.id ? 'opacity-60' : ''}`}
                  >
                    <p className="text-sm font-medium leading-snug">
                      {t.task_number != null && <span className="text-muted-foreground">#{t.task_number} </span>}
                      {t.title}
                    </p>
                    <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                      {t.category && (
                        <span className="text-xs text-muted-foreground capitalize">{t.category}</span>
                      )}
                      {t.status && (
                        <Badge className={`text-xs h-4 px-1.5 ${STATUS_COLORS[t.status] ?? 'bg-gray-100 text-gray-700'}`}>
                          {t.status}
                        </Badge>
                      )}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* Chat panel */}
        <div className={`${activeTask ? 'flex' : 'hidden md:flex'} flex-col flex-1 overflow-hidden`}>
          {activeTask ? (
            <ChatPanel
              task={activeTask}
              onBack={() => setActiveTask(null)}
            />
          ) : (
            <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
              Select a task to view the conversation
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default VendorPortal;
