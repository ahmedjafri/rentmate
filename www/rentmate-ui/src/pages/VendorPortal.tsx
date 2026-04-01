import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Wrench, LogOut, ArrowLeft } from 'lucide-react';
import { ChatMessageBubble } from '@/components/chat/ChatMessage';
import { ChatInput } from '@/components/chat/ChatInput';
import { ChatMessage, ChatSenderType } from '@/data/mockData';
import { getVendorToken, isVendorAuthenticated, vendorLogout } from '@/lib/vendorAuth';

// ─── types ────────────────────────────────────────────────────────────────────

interface VendorMe {
  id: string;
  name: string;
  company?: string;
  vendor_type?: string;
  email?: string;
}

interface VendorTask {
  id: string;
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

// ─── chat panel ───────────────────────────────────────────────────────────────

function ChatPanel({ task, onBack }: { task: TaskDetail; onBack: () => void }) {
  const [messages, setMessages] = useState<TaskMessage[]>(task.messages);
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Scroll to bottom when messages change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Poll for new messages every 5s
  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const r = await fetch(`/api/vendor/tasks/${task.id}`, { headers: authHeaders() });
        if (r.ok) {
          const data: TaskDetail = await r.json();
          setMessages(data.messages);
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
          <p className="font-semibold text-sm leading-tight truncate">{task.title}</p>
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
        <div ref={bottomRef} />
      </div>

      <ChatInput onSend={handleSend} disabled={sending} />
    </div>
  );
}

// ─── main component ───────────────────────────────────────────────────────────

const VendorPortal = () => {
  const navigate = useNavigate();
  const [me, setMe] = useState<VendorMe | null>(null);
  const [tasks, setTasks] = useState<VendorTask[]>([]);
  const [activeTask, setActiveTask] = useState<TaskDetail | null>(null);
  const [loadingTask, setLoadingTask] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isVendorAuthenticated()) {
      setError('Please use your invite link to access the vendor portal.');
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
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [navigate]);

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
    setError('You have been logged out. Use your invite link to sign back in.');
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
          </div>
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
                    <p className="text-sm font-medium leading-snug">{t.title}</p>
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
