import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Home, LogOut, ArrowLeft } from 'lucide-react';
import { ChatMessageBubble } from '@/components/chat/ChatMessage';
import { ChatInput } from '@/components/chat/ChatInput';
import { ChatMessage, ChatSenderType } from '@/data/mockData';
import { getTenantToken, isTenantAuthenticated, tenantLogout } from '@/lib/tenantAuth';

// ─── types ────────────────────────────────────────────────────────────────────

interface TenantMe {
  id: string;
  name: string;
  email?: string;
  phone?: string;
}

interface TenantTask {
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

interface TaskDetail extends TenantTask {
  messages: TaskMessage[];
  typing?: boolean;
}

// ─── helpers ──────────────────────────────────────────────────────────────────

function authHeaders() {
  return { Authorization: `Bearer ${getTenantToken()}` };
}

function jsonHeaders() {
  return { ...authHeaders(), 'Content-Type': 'application/json' };
}

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-blue-100 text-blue-800',
  suggested: 'bg-yellow-100 text-yellow-800',
  resolved: 'bg-green-100 text-green-800',
  cancelled: 'bg-gray-100 text-gray-700',
};

function taskMessageToChat(m: TaskMessage): ChatMessage {
  let senderType: ChatSenderType;
  if (m.sender_type === 'tenant') {
    senderType = 'manager'; // right-aligned, primary — tenant's own messages
  } else if (m.is_ai) {
    senderType = 'ai';
  } else {
    senderType = 'tenant'; // left-aligned muted — property manager messages
  }
  return {
    id: m.id,
    role: m.sender_type === 'tenant' ? 'user' : 'assistant',
    content: m.body,
    senderName: m.sender_type === 'tenant' ? 'You' : m.sender_name,
    senderType,
    timestamp: new Date(m.sent_at),
    messageType: 'message',
  };
}

// ─── chat panel ───────────────────────────────────────────────────────────────

function ChatPanel({ task, onBack }: { task: TaskDetail; onBack: () => void }) {
  const [messages, setMessages] = useState<TaskMessage[]>(task.messages);
  const [sending, setSending] = useState(false);
  const [typing, setTyping] = useState(task.typing ?? false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, typing]);

  // Poll for new messages every 5s
  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const r = await fetch(`/api/tenant/tasks/${task.id}`, { headers: authHeaders() });
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
      const r = await fetch(`/api/tenant/tasks/${task.id}/messages`, {
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
              <Home className="h-3.5 w-3.5" />
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

// ─── main page ────────────────────────────────────────────────────────────────

const TenantPortal = () => {
  const navigate = useNavigate();
  const [me, setMe] = useState<TenantMe | null>(null);
  const [tasks, setTasks] = useState<TenantTask[]>([]);
  const [activeTask, setActiveTask] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingTask, setLoadingTask] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isTenantAuthenticated()) {
      setError('Your session has expired. Please use your portal link to sign back in.');
      setLoading(false);
      return;
    }

    Promise.all([
      fetch('/api/tenant/me', { headers: authHeaders() }).then((r) => {
        if (!r.ok) throw new Error('Failed to load profile');
        return r.json();
      }),
      fetch('/api/tenant/tasks', { headers: authHeaders() }).then((r) => {
        if (!r.ok) throw new Error('Failed to load requests');
        return r.json();
      }),
    ])
      .then(([meData, tasksData]) => {
        setMe(meData);
        setTasks(tasksData);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [navigate]);

  const openTask = async (taskId: string) => {
    setLoadingTask(taskId);
    try {
      const r = await fetch(`/api/tenant/tasks/${taskId}`, { headers: authHeaders() });
      if (!r.ok) throw new Error('Failed to load request');
      setActiveTask(await r.json());
    } catch { /* ignore */ } finally {
      setLoadingTask(null);
    }
  };

  const handleLogout = () => {
    tenantLogout();
    setError('You have been logged out. Use your portal link to sign back in.');
    setMe(null);
    setTasks([]);
    setActiveTask(null);
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
    <div className="min-h-screen flex flex-col bg-muted/30">
      {/* Top bar */}
      <header className="bg-background border-b px-4 py-3 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="bg-primary/10 rounded-full p-1.5">
            <Home className="h-4 w-4 text-primary" />
          </div>
          <span className="font-semibold text-sm">{me?.name}</span>
        </div>
        <Button variant="ghost" size="sm" onClick={handleLogout} className="gap-1.5 text-muted-foreground">
          <LogOut className="h-3.5 w-3.5" />
          Sign out
        </Button>
      </header>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Request list */}
        <div className={`${activeTask ? 'hidden md:flex' : 'flex'} flex-col w-full md:w-72 lg:w-80 border-r bg-background shrink-0`}>
          <div className="px-4 py-3 border-b">
            <h2 className="font-semibold text-sm">Your Requests</h2>
          </div>
          <div className="flex-1 overflow-y-auto">
            {tasks.length === 0 ? (
              <p className="text-sm text-muted-foreground p-4">No active requests.</p>
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
            <ChatPanel task={activeTask} onBack={() => setActiveTask(null)} />
          ) : (
            <div className="flex-1 flex items-center justify-center text-muted-foreground">
              <div className="text-center">
                <Home className="h-8 w-8 mx-auto mb-2 opacity-40" />
                <p className="text-sm">Select a request to view the conversation</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default TenantPortal;
