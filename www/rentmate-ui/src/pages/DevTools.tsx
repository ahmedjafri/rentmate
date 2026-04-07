import { useState, useRef, useEffect, useCallback } from 'react';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Bot, User, ExternalLink, Terminal, Trash2, Activity, ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { getToken, authFetch } from '@/lib/auth';
import { toast } from 'sonner';
import { Link } from 'react-router-dom';

interface ChatEntry {
  role: 'tenant' | 'agent';
  text: string;
  taskId?: string;
  taskCreated?: boolean;
}

interface TraceEntry {
  id: string;
  timestamp: string;
  trace_type: string;
  source: string;
  task_id: string | null;
  tool_name: string | null;
  summary: string;
  detail: string | null;
  suggestion_id: string | null;
}

const TRACE_COLORS: Record<string, string> = {
  tool_call: 'bg-blue-100 text-blue-800',
  tool_result: 'bg-green-100 text-green-800',
  llm_reply: 'bg-purple-100 text-purple-800',
  suggestion_created: 'bg-yellow-100 text-yellow-800',
  suggestion_executed: 'bg-orange-100 text-orange-800',
  error: 'bg-red-100 text-red-800',
};

function TracesPanel() {
  const [traces, setTraces] = useState<TraceEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>('');

  const loadTraces = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: '200' });
      if (filter) params.set('trace_type', filter);
      const res = await fetch(`/dev/traces?${params}`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      if (res.ok) setTraces(await res.json());
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { loadTraces(); }, [loadTraces]);

  // Auto-refresh every 5s
  useEffect(() => {
    const id = setInterval(loadTraces, 5000);
    return () => clearInterval(id);
  }, [loadTraces]);

  const copyText = (text: string, label: string = 'Copied') => {
    try {
      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;left:-9999px';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      toast.success(label);
    } catch {
      toast.error('Failed to copy');
    }
  };

  const copyAllTraces = () => {
    const text = traces.map(t =>
      `[${t.timestamp}] ${t.trace_type} (${t.source}) ${t.summary}${t.detail ? '\n' + t.detail : ''}`
    ).join('\n\n');
    copyText(text, `Copied ${traces.length} traces`);
  };

  const formatTime = (ts: string) => {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  const parseDetail = (detail: string | null): string => {
    if (!detail) return '';
    try {
      return JSON.stringify(JSON.parse(detail), null, 2);
    } catch {
      return detail;
    }
  };

  return (
    <Card className="p-4 rounded-xl">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-muted-foreground" />
          <h2 className="font-semibold text-sm">Agent Traces</h2>
          <Badge variant="outline" className="text-[10px]">{traces.length}</Badge>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="text-xs border rounded px-2 py-1 bg-background"
            value={filter}
            onChange={e => setFilter(e.target.value)}
          >
            <option value="">All types</option>
            <option value="tool_call">Tool calls</option>
            <option value="tool_result">Tool results</option>
            <option value="llm_reply">LLM replies</option>
            <option value="suggestion_created">Suggestions created</option>
            <option value="suggestion_executed">Suggestions executed</option>
            <option value="error">Errors</option>
          </select>
          <Button variant="ghost" size="sm" onClick={loadTraces} disabled={loading} className="h-7 w-7 p-0">
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          </Button>
          {traces.length > 0 && (
            <Button variant="ghost" size="sm" onClick={copyAllTraces} className="h-7 text-[10px] px-2">
              Copy All
            </Button>
          )}
        </div>
      </div>

      <div className="max-h-[400px] overflow-y-auto space-y-1">
        {traces.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-8">No traces yet. Agent activity will appear here.</p>
        )}
        {traces.map(t => (
          <div key={t.id} className="border rounded-lg">
            <button
              className="w-full text-left px-3 py-2 flex items-center gap-2 hover:bg-muted/50 transition-colors"
              onClick={() => setExpandedId(expandedId === t.id ? null : t.id)}
            >
              <span className="text-[10px] text-muted-foreground font-mono w-16 shrink-0">
                {formatTime(t.timestamp)}
              </span>
              <Badge className={cn("text-[9px] h-4 px-1.5 shrink-0", TRACE_COLORS[t.trace_type] ?? 'bg-gray-100 text-gray-700')}>
                {t.trace_type}
              </Badge>
              {t.source && (
                <Badge variant="outline" className="text-[9px] h-4 px-1.5 shrink-0">{t.source}</Badge>
              )}
              <span className="text-xs truncate flex-1">{t.summary}</span>
              {t.detail && (
                expandedId === t.id
                  ? <ChevronUp className="h-3 w-3 text-muted-foreground shrink-0" />
                  : <ChevronDown className="h-3 w-3 text-muted-foreground shrink-0" />
              )}
            </button>
            {expandedId === t.id && t.detail && (
              <div className="px-3 pb-2">
                <pre className="text-[10px] bg-muted/50 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono max-h-48 overflow-y-auto">
                  {parseDetail(t.detail)}
                </pre>
                {t.task_id && (
                  <p className="text-[10px] text-muted-foreground mt-1">Task: {t.task_id}</p>
                )}
                {t.suggestion_id && (
                  <p className="text-[10px] text-muted-foreground">Suggestion: {t.suggestion_id}</p>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

async function fetchDevHistory(tenantId: string): Promise<{ taskId: string | null; messages: ChatEntry[] }> {
  const res = await fetch(`/dev/history/${tenantId}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data: { task_id: string | null; messages: { role: string; text: string; task_id: string }[] } = await res.json();
  return {
    taskId: data.task_id,
    messages: data.messages.map(m => ({
      role: m.role as 'tenant' | 'agent',
      text: m.text,
      taskId: m.task_id,
    })),
  };
}

const DevTools = () => {
  const { tenants } = useApp();
  const [tenantId, setTenantId] = useState('');
  const [channelType, setChannelType] = useState<'sms' | 'email'>('sms');
  const [forceNew, setForceNew] = useState(false);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [history, setHistory] = useState<ChatEntry[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [history]);

  const loadHistory = useCallback(async (id: string) => {
    if (!id) { setHistory([]); return; }
    setHistoryLoading(true);
    try {
      const { messages } = await fetchDevHistory(id);
      setHistory(messages);
    } catch {
      // non-fatal — start with empty history
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const handleTenantChange = (id: string) => {
    setTenantId(id);
    setForceNew(false);
    loadHistory(id);
  };

  const handleSend = async () => {
    if (!tenantId) { toast.error('Select a tenant first'); return; }
    if (!input.trim()) return;

    const userText = input.trim();
    setInput('');
    setHistory(prev => [...prev, { role: 'tenant', text: userText }]);
    setLoading(true);

    try {
      const res = await fetch('/dev/simulate-inbound', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${getToken()}`,
        },
        body: JSON.stringify({
          tenant_id: tenantId,
          channel_type: channelType,
          message: userText,
          force_new: forceNew,
        }),
      });

      if (!res.ok) {
        const err = await res.text();
        throw new Error(`HTTP ${res.status}: ${err}`);
      }

      const data: { task_id: string; reply: string; task_created: boolean } = await res.json();

      setHistory(prev => [
        ...prev,
        { role: 'agent', text: data.reply, taskId: data.task_id, taskCreated: data.task_created },
      ]);
      if (forceNew) setForceNew(false);
    } catch (err) {
      toast.error(`Simulation failed: ${(err as Error).message}`);
      setHistory(prev => prev.slice(0, -1));
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setHistory([]);
    setForceNew(true);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const selectedTenant = tenants.find(t => t.id === tenantId);
  const [wiping, setWiping] = useState<string | null>(null);

  const handleWipe = async (target: 'tasks' | 'suggestions' | 'chats') => {
    if (!confirm(`Are you sure you want to delete ALL ${target}? This cannot be undone.`)) return;
    setWiping(target);
    try {
      const res = await authFetch(`/dev/wipe-${target}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (target === 'tasks') {
        toast.success(`Deleted ${data.deleted_tasks} tasks and ${data.deleted_conversations} conversations`);
      } else if (target === 'suggestions') {
        toast.success(`Deleted ${data.deleted_suggestions} suggestions and ${data.deleted_conversations} conversations`);
      } else {
        toast.success(`Deleted ${data.deleted_conversations} conversations (unlinked ${data.unlinked_tasks} tasks)`);
      }
      if (tenantId) loadHistory(tenantId);
    } catch (err) {
      toast.error(`Wipe failed: ${(err as Error).message}`);
    } finally {
      setWiping(null);
    }
  };

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-4">
      <div>
        <div className="flex items-center gap-2">
          <Terminal className="h-5 w-5 text-muted-foreground" />
          <h1 className="text-2xl font-bold">Developer Tools</h1>
        </div>
        <p className="text-sm text-muted-foreground mt-1">
          Simulate inbound tenant messages. Chat history is stored in the DB and restored on reload.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Config panel */}
        <Card className="p-4 space-y-4 rounded-xl">
          <h2 className="font-semibold text-sm">Simulator Config</h2>

          <div className="space-y-2">
            <Label>Tenant</Label>
            <Select value={tenantId} onValueChange={handleTenantChange}>
              <SelectTrigger>
                <SelectValue placeholder="Select tenant…" />
              </SelectTrigger>
              <SelectContent>
                {tenants.map(t => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name}
                    {t.email ? ` — ${t.email}` : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selectedTenant && (
              <p className="text-xs text-muted-foreground">
                Unit: {selectedTenant.unit || 'none'} · {selectedTenant.email || 'no email'}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label>Channel</Label>
            <Select value={channelType} onValueChange={v => setChannelType(v as 'sms' | 'email')}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="sms">SMS</SelectItem>
                <SelectItem value="email">Email</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="force-new"
              checked={forceNew}
              onChange={e => setForceNew(e.target.checked)}
              className="h-4 w-4"
            />
            <Label htmlFor="force-new" className="text-sm font-normal cursor-pointer">
              Force new conversation
            </Label>
          </div>

          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={handleReset}
            disabled={!tenantId}
          >
            New chat
          </Button>
        </Card>

        {/* Chat panel */}
        <Card className="md:col-span-2 p-4 rounded-xl flex flex-col" style={{ minHeight: '500px' }}>
          <h2 className="font-semibold text-sm mb-3">
            Chat (as tenant{selectedTenant ? `: ${selectedTenant.name}` : ''})
          </h2>

          <div className="flex-1 overflow-y-auto space-y-3 mb-3 pr-1">
            {historyLoading && (
              <p className="text-sm text-muted-foreground text-center py-8">Loading history…</p>
            )}
            {!historyLoading && history.length === 0 && (
              <p className="text-sm text-muted-foreground text-center py-8">
                {tenantId
                  ? 'No previous simulation found. Type a message below to start.'
                  : 'Select a tenant to start simulating.'}
              </p>
            )}
            {!historyLoading && history.map((entry, i) => (
              <div
                key={i}
                className={cn(
                  'flex gap-2',
                  entry.role === 'tenant' ? 'justify-end' : 'justify-start',
                )}
              >
                {entry.role === 'agent' && (
                  <div className="h-7 w-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-0.5">
                    <Bot className="h-4 w-4 text-primary" />
                  </div>
                )}

                <div className="max-w-[75%] space-y-1">
                  <div
                    className={cn(
                      'rounded-2xl px-3 py-2 text-sm',
                      entry.role === 'tenant'
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-muted text-foreground',
                    )}
                  >
                    {entry.text}
                  </div>

                  {entry.role === 'agent' && entry.taskId && (
                    <div className="flex items-center gap-1">
                      <Badge variant="outline" className="text-[10px] px-1.5 py-0.5">
                        {entry.taskCreated && <span className="text-green-600 mr-1">new ·</span>}
                        Task
                      </Badge>
                      <Link
                        to="/action-desk"
                        className="text-[10px] text-muted-foreground hover:underline flex items-center gap-0.5"
                        title={`Task ${entry.taskId}`}
                      >
                        Open in Action Desk
                        <ExternalLink className="h-2.5 w-2.5" />
                      </Link>
                    </div>
                  )}
                </div>

                {entry.role === 'tenant' && (
                  <div className="h-7 w-7 rounded-full bg-muted flex items-center justify-center shrink-0 mt-0.5">
                    <User className="h-4 w-4 text-muted-foreground" />
                  </div>
                )}
              </div>
            ))}
            {loading && (
              <div className="flex gap-2 justify-start">
                <div className="h-7 w-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                  <Bot className="h-4 w-4 text-primary animate-pulse" />
                </div>
                <div className="rounded-2xl px-3 py-2 text-sm bg-muted text-muted-foreground">
                  Thinking…
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="flex gap-2">
            <Textarea
              className="resize-none text-sm"
              rows={2}
              placeholder="Type a message as the tenant… (Enter to send)"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading || historyLoading}
            />
            <Button
              onClick={handleSend}
              disabled={loading || historyLoading || !input.trim() || !tenantId}
              className="self-end"
            >
              Send
            </Button>
          </div>
        </Card>
      </div>

      {/* Traces */}
      <TracesPanel />

      {/* Danger zone */}
      <Card className="p-4 rounded-xl border-destructive/30">
        <h2 className="font-semibold text-sm text-destructive mb-3">Danger Zone</h2>
        <div className="flex flex-wrap gap-3">
          <Button
            variant="destructive"
            size="sm"
            disabled={wiping !== null}
            onClick={() => handleWipe('tasks')}
          >
            <Trash2 className="h-3.5 w-3.5 mr-1.5" />
            {wiping === 'tasks' ? 'Wiping…' : 'Wipe all tasks'}
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={wiping !== null}
            onClick={() => handleWipe('suggestions')}
          >
            <Trash2 className="h-3.5 w-3.5 mr-1.5" />
            {wiping === 'suggestions' ? 'Wiping…' : 'Wipe all suggestions'}
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={wiping !== null}
            onClick={() => handleWipe('chats')}
          >
            <Trash2 className="h-3.5 w-3.5 mr-1.5" />
            {wiping === 'chats' ? 'Wiping…' : 'Wipe all chats'}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground mt-2">
          Permanently deletes data from the database. Tasks wipe also removes linked suggestions and conversations.
          Suggestions wipe removes all suggestions and their AI conversations.
          Chats wipe removes all conversations and unlinks them from tasks.
        </p>
      </Card>
    </div>
  );
};

export default DevTools;
