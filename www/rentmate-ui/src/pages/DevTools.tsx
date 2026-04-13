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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Bot, User, ExternalLink, Terminal, Trash2, Activity, ChevronDown, RefreshCw, Copy } from 'lucide-react';
import { cn } from '@/lib/utils';
import { authFetch } from '@/lib/auth';
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
  conversation_id: string | null;
  tool_name: string | null;
  summary: string;
  detail: string | null;
  suggestion_id: string | null;
}

interface TraceFilterOption {
  id: string;
  title?: string;
  subject?: string;
  updated_at?: string | null;
  task_id?: string | null;
}

interface TraceDetailEntry extends TraceEntry {
  conversation_id: string | null;
  raw_detail: string | null;
  detail: unknown;
}

interface MemoryItemEntry {
  id: string;
  source_type: string;
  source_id: string;
  entity_type: string;
  entity_id: string;
  visibility: string;
  title: string | null;
  content: string;
  metadata: Record<string, unknown>;
  updated_at: string | null;
}

interface RankedMemoryEntry {
  memory_item_id: string;
  source_type: string;
  source_id: string;
  entity_type: string;
  entity_id: string;
  title: string | null;
  content: string;
  metadata: Record<string, unknown>;
  heuristic_score: number;
  vector_score: number;
  final_score: number;
  reasons: string[];
}

const TRACE_COLORS: Record<string, string> = {
  tool_call: 'bg-blue-100 text-blue-800',
  tool_result: 'bg-green-100 text-green-800',
  llm_reply: 'bg-purple-100 text-purple-800',
  suggestion_created: 'bg-yellow-100 text-yellow-800',
  suggestion_executed: 'bg-orange-100 text-orange-800',
  memory_sync: 'bg-cyan-100 text-cyan-800',
  memory_rank: 'bg-indigo-100 text-indigo-800',
  error: 'bg-red-100 text-red-800',
};

function TracesPanel() {
  const [traces, setTraces] = useState<TraceEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<string>('all');
  const [sourceFilter, setSourceFilter] = useState<string>('all');
  const [taskFilter, setTaskFilter] = useState<string>('all');
  const [conversationFilter, setConversationFilter] = useState<string>('all');
  const [taskOptions, setTaskOptions] = useState<TraceFilterOption[]>([]);
  const [chatOptions, setChatOptions] = useState<TraceFilterOption[]>([]);
  const [selectedTrace, setSelectedTrace] = useState<TraceDetailEntry | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailSection, setDetailSection] = useState<'overview' | 'context' | 'retrieval' | 'io' | 'reasoning' | 'raw'>('overview');

  const loadTraces = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: '200' });
      if (filter !== 'all') params.set('trace_type', filter);
      if (sourceFilter !== 'all') params.set('source', sourceFilter);
      if (taskFilter !== 'all') params.set('task_id', taskFilter);
      if (conversationFilter !== 'all') params.set('conversation_id', conversationFilter);
      const res = await authFetch(`/dev/traces?${params}`);
      if (res.ok) setTraces(await res.json());
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, [conversationFilter, filter, sourceFilter, taskFilter]);

  useEffect(() => { loadTraces(); }, [loadTraces]);

  useEffect(() => {
    const loadFilters = async () => {
      try {
        const [tasksRes, chatsRes] = await Promise.all([
          authFetch('/dev/trace-filters/tasks'),
          authFetch('/dev/trace-filters/chats'),
        ]);
        if (tasksRes.ok) setTaskOptions(await tasksRes.json());
        if (chatsRes.ok) setChatOptions(await chatsRes.json());
      } catch {
        // ignore
      }
    };
    void loadFilters();
  }, []);

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

  const copyTrace = (trace: TraceEntry) => {
    const text = `[${trace.timestamp}] ${trace.trace_type} (${trace.source}) ${trace.summary}${trace.detail ? '\n' + parseDetail(trace.detail) : ''}`;
    copyText(text, 'Copied trace');
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

  const openTrace = async (traceId: string) => {
    setSelectedTrace(null);
    setDetailLoading(true);
    setDetailSection('overview');
    try {
      const res = await authFetch(`/dev/traces/${traceId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSelectedTrace(await res.json());
    } catch (err) {
      toast.error(`Failed to load trace: ${(err as Error).message}`);
    } finally {
      setDetailLoading(false);
    }
  };

  const sourceOptions = Array.from(new Set(traces.map(t => t.source).filter(Boolean))) as string[];
  const detailEnvelope = (selectedTrace?.detail && typeof selectedTrace.detail === 'object')
    ? selectedTrace.detail as Record<string, unknown>
    : null;
  const traceContext = (detailEnvelope?.trace_context && typeof detailEnvelope.trace_context === 'object')
    ? detailEnvelope.trace_context as Record<string, unknown>
    : detailEnvelope;
  const contextBlock = (detailEnvelope?.context && typeof detailEnvelope.context === 'object')
    ? detailEnvelope.context as Record<string, unknown>
    : ((traceContext?.context && typeof traceContext.context === 'object') ? traceContext.context as Record<string, unknown> : null);
  const retrievalBlock = (detailEnvelope?.retrieval && typeof detailEnvelope.retrieval === 'object')
    ? detailEnvelope.retrieval as Record<string, unknown>
    : ((contextBlock?.retrieval && typeof contextBlock.retrieval === 'object') ? contextBlock.retrieval as Record<string, unknown> : null);
  const normalizedRetrievalBlock = retrievalBlock
    ?? ((detailEnvelope && 'request' in detailEnvelope) ? {
      request: (detailEnvelope as Record<string, unknown>).request,
      items: (detailEnvelope as Record<string, unknown>).items || (detailEnvelope as Record<string, unknown>).top_items,
      ordered_indices: (detailEnvelope as Record<string, unknown>).ordered_indices,
      reason: (detailEnvelope as Record<string, unknown>).reason,
    } as Record<string, unknown> : null);
  const reasoningBlock = (detailEnvelope?.reasoning && typeof detailEnvelope.reasoning === 'object')
    ? detailEnvelope.reasoning as Record<string, unknown>
    : ((traceContext?.reasoning && typeof traceContext.reasoning === 'object') ? traceContext.reasoning as Record<string, unknown> : null);
  const messagesPayload = Array.isArray(detailEnvelope?.messages_payload)
    ? detailEnvelope?.messages_payload as Array<Record<string, unknown>>
    : (Array.isArray(traceContext?.messages_payload) ? traceContext?.messages_payload as Array<Record<string, unknown>> : []);
  const messageBreakdown = (detailEnvelope?.messages_breakdown && typeof detailEnvelope.messages_breakdown === 'object')
    ? detailEnvelope.messages_breakdown as Record<string, unknown>
    : ((traceContext?.messages_breakdown && typeof traceContext.messages_breakdown === 'object') ? traceContext.messages_breakdown as Record<string, unknown> : null);
  const sectionButtons: Array<{ key: typeof detailSection; label: string }> = [
    { key: 'overview', label: 'Overview' },
    { key: 'context', label: 'Context' },
    { key: 'retrieval', label: 'Retrieval' },
    { key: 'io', label: 'Tool / Model I/O' },
    { key: 'reasoning', label: 'Reasoning' },
    { key: 'raw', label: 'Raw JSON' },
  ];

  return (
    <Card className="p-4 rounded-xl">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-muted-foreground" />
          <h2 className="font-semibold text-sm">Agent Traces</h2>
          <Badge variant="outline" className="text-[10px]">{traces.length}</Badge>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <select className="text-xs border rounded px-2 py-1 bg-background" value={filter} onChange={e => setFilter(e.target.value)}>
            <option value="all">All types</option>
            <option value="tool_call">Tool calls</option>
            <option value="tool_result">Tool results</option>
            <option value="llm_request">LLM requests</option>
            <option value="llm_reply">LLM replies</option>
            <option value="suggestion_created">Suggestions created</option>
            <option value="suggestion_executed">Suggestions executed</option>
            <option value="memory_sync">Memory sync</option>
            <option value="memory_rank">Memory rank</option>
            <option value="memory_rerank">Memory rerank</option>
            <option value="error">Errors</option>
          </select>
          <select className="text-xs border rounded px-2 py-1 bg-background max-w-[180px]" value={taskFilter} onChange={e => setTaskFilter(e.target.value)}>
            <option value="all">All tasks</option>
            {taskOptions.map(task => (
              <option key={task.id} value={task.id}>{task.title || task.id}</option>
            ))}
          </select>
          <select className="text-xs border rounded px-2 py-1 bg-background max-w-[180px]" value={conversationFilter} onChange={e => setConversationFilter(e.target.value)}>
            <option value="all">All chats</option>
            {chatOptions.map(chat => (
              <option key={chat.id} value={chat.id}>{chat.subject || chat.id}</option>
            ))}
          </select>
          <select className="text-xs border rounded px-2 py-1 bg-background" value={sourceFilter} onChange={e => setSourceFilter(e.target.value)}>
            <option value="all">All sources</option>
            {sourceOptions.map(source => (
              <option key={source} value={source}>{source}</option>
            ))}
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
            <div className="flex items-center gap-1 px-1 py-1">
              <button
                className="min-w-0 flex-1 text-left px-2 py-1 flex items-center gap-2 hover:bg-muted/50 transition-colors rounded-md"
                onClick={() => void openTrace(t.id)}
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
                {t.task_id && (
                  <Badge variant="outline" className="text-[9px] h-4 px-1.5 shrink-0">task</Badge>
                )}
                {t.conversation_id && (
                  <Badge variant="outline" className="text-[9px] h-4 px-1.5 shrink-0">chat</Badge>
                )}
                <span className="text-xs truncate flex-1">{t.summary}</span>
                <ChevronDown className="h-3 w-3 text-muted-foreground shrink-0" />
              </button>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 w-7 shrink-0 p-0"
                aria-label={`Copy trace ${t.id}`}
                onClick={(e) => {
                  e.stopPropagation();
                  copyTrace(t);
                }}
              >
                <Copy className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        ))}
      </div>

      <Dialog open={!!selectedTrace || detailLoading} onOpenChange={open => { if (!open) { setSelectedTrace(null); setDetailLoading(false); } }}>
        <DialogContent className="max-w-5xl h-[88vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>Trace detail</DialogTitle>
            <DialogDescription>
              Inspect the exact trace envelope, model-visible context, retrieval inputs, ranked memory, and tool/model I/O for this step.
            </DialogDescription>
          </DialogHeader>
          {detailLoading && <p className="text-sm text-muted-foreground">Loading trace…</p>}
          {!detailLoading && selectedTrace && (
            <div className="flex-1 min-h-0 flex flex-col gap-3">
              <div className="flex flex-wrap gap-2">
                {sectionButtons.map(section => (
                  <Button
                    key={section.key}
                    size="sm"
                    variant={detailSection === section.key ? 'default' : 'outline'}
                    onClick={() => setDetailSection(section.key)}
                  >
                    {section.label}
                  </Button>
                ))}
              </div>

              <div className="flex-1 min-h-0 overflow-y-auto space-y-3">
                {detailSection === 'overview' && (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                      <div className="border rounded p-2"><div className="text-muted-foreground">Type</div><div>{selectedTrace.trace_type}</div></div>
                      <div className="border rounded p-2"><div className="text-muted-foreground">Source</div><div>{selectedTrace.source || 'n/a'}</div></div>
                      <div className="border rounded p-2"><div className="text-muted-foreground">Task</div><div className="break-all">{selectedTrace.task_id || 'n/a'}</div></div>
                      <div className="border rounded p-2"><div className="text-muted-foreground">Chat</div><div className="break-all">{selectedTrace.conversation_id || 'n/a'}</div></div>
                    </div>
                    <div className="border rounded p-3">
                      <div className="text-xs text-muted-foreground mb-1">Summary</div>
                      <div className="text-sm">{selectedTrace.summary}</div>
                    </div>
                    {messageBreakdown && (
                      <div className="border rounded p-3 space-y-2">
                        <div className="text-xs text-muted-foreground">Message breakdown</div>
                        <pre className="text-[10px] bg-muted/50 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono">
                          {JSON.stringify(messageBreakdown, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                )}

                {detailSection === 'context' && (
                  <div className="space-y-3">
                    {contextBlock && typeof contextBlock.text === 'string' && (
                      <div className="border rounded p-3">
                        <div className="text-xs text-muted-foreground mb-1">Full context window</div>
                        <pre className="text-[10px] bg-muted/50 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono">
                          {contextBlock.text}
                        </pre>
                      </div>
                    )}
                    {Array.isArray(contextBlock?.sections) && contextBlock.sections.map((section, idx) => (
                      <div key={idx} className="border rounded p-3 space-y-2">
                        <div className="flex items-center gap-2">
                          <Badge variant="outline" className="text-[10px]">{String((section as Record<string, unknown>).section_type || 'section')}</Badge>
                          <div className="text-sm font-medium">{String((section as Record<string, unknown>).title || `Section ${idx + 1}`)}</div>
                        </div>
                        <pre className="text-[10px] bg-muted/50 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono">
                          {String((section as Record<string, unknown>).content || '')}
                        </pre>
                        <pre className="text-[10px] bg-muted/30 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono">
                          {JSON.stringify((section as Record<string, unknown>).origin || {}, null, 2)}
                        </pre>
                      </div>
                    ))}
                    {!contextBlock && <p className="text-sm text-muted-foreground">No structured context captured for this trace.</p>}
                  </div>
                )}

                {detailSection === 'retrieval' && (
                  <div className="space-y-3">
                    {normalizedRetrievalBlock ? (
                      <>
                        <div className="border rounded p-3">
                          <div className="text-xs text-muted-foreground mb-1">Retriever input</div>
                          <pre className="text-[10px] bg-muted/50 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono">
                            {JSON.stringify((normalizedRetrievalBlock as Record<string, unknown>).request || normalizedRetrievalBlock, null, 2)}
                          </pre>
                        </div>
                        {Array.isArray((normalizedRetrievalBlock as Record<string, unknown>).items) && (
                          <div className="space-y-2">
                            {((normalizedRetrievalBlock as Record<string, unknown>).items as Array<Record<string, unknown>>).map((item, idx) => (
                              <div key={idx} className="border rounded p-3 space-y-2">
                                <div className="flex items-center gap-2">
                                  <Badge className="bg-slate-900 text-white text-[10px]">#{idx + 1}</Badge>
                                  <Badge variant="outline" className="text-[10px]">{String(item.source_type || 'item')}</Badge>
                                  <span className="text-sm font-medium">{String(item.title || item.memory_item_id || 'untitled')}</span>
                                </div>
                                <div className="grid grid-cols-3 gap-2 text-[10px] text-muted-foreground">
                                  <span>Final: {Number(item.final_score || 0).toFixed(2)}</span>
                                  <span>Vector: {Number(item.vector_score || 0).toFixed(2)}</span>
                                  <span>Heuristic: {Number(item.heuristic_score || 0).toFixed(2)}</span>
                                </div>
                                <pre className="text-[10px] bg-muted/50 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono">
                                  {String(item.content || '')}
                                </pre>
                                <pre className="text-[10px] bg-muted/30 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono">
                                  {JSON.stringify(item.reasons || [], null, 2)}
                                </pre>
                              </div>
                            ))}
                          </div>
                        )}
                      </>
                    ) : (
                      <p className="text-sm text-muted-foreground">No retrieval metadata captured for this trace.</p>
                    )}
                  </div>
                )}

                {detailSection === 'io' && (
                  <div className="space-y-3">
                    {messagesPayload.length > 0 && (
                      <div className="border rounded p-3">
                        <div className="text-xs text-muted-foreground mb-1">Messages payload sent to model</div>
                        <pre className="text-[10px] bg-muted/50 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono">
                          {JSON.stringify(messagesPayload, null, 2)}
                        </pre>
                      </div>
                    )}
                    {'args' in (detailEnvelope || {}) && (
                      <div className="border rounded p-3">
                        <div className="text-xs text-muted-foreground mb-1">Tool args</div>
                        <pre className="text-[10px] bg-muted/50 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono">
                          {JSON.stringify((detailEnvelope as Record<string, unknown>).args || {}, null, 2)}
                        </pre>
                      </div>
                    )}
                    {'result' in (detailEnvelope || {}) && (
                      <div className="border rounded p-3">
                        <div className="text-xs text-muted-foreground mb-1">Tool result</div>
                        <pre className="text-[10px] bg-muted/50 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono">
                          {JSON.stringify((detailEnvelope as Record<string, unknown>).result, null, 2)}
                        </pre>
                      </div>
                    )}
                    {'reply' in (detailEnvelope || {}) && (
                      <div className="border rounded p-3">
                        <div className="text-xs text-muted-foreground mb-1">LLM reply</div>
                        <pre className="text-[10px] bg-muted/50 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono">
                          {String((detailEnvelope as Record<string, unknown>).reply || '')}
                        </pre>
                      </div>
                    )}
                    {messagesPayload.length === 0 && !('args' in (detailEnvelope || {})) && !('result' in (detailEnvelope || {})) && !('reply' in (detailEnvelope || {})) && (
                      <p className="text-sm text-muted-foreground">No model/tool I/O envelope captured for this trace.</p>
                    )}
                  </div>
                )}

                {detailSection === 'reasoning' && (
                  <div className="space-y-3">
                    {reasoningBlock ? (
                      <pre className="text-[10px] bg-muted/50 rounded p-3 overflow-x-auto whitespace-pre-wrap font-mono">
                        {JSON.stringify(reasoningBlock, null, 2)}
                      </pre>
                    ) : (
                      <p className="text-sm text-muted-foreground">No provider reasoning trace available for this response.</p>
                    )}
                  </div>
                )}

                {detailSection === 'raw' && (
                  <pre className="text-[10px] bg-muted/50 rounded p-3 overflow-x-auto whitespace-pre-wrap font-mono">
                    {selectedTrace.raw_detail ? parseDetail(selectedTrace.raw_detail) : JSON.stringify(selectedTrace.detail, null, 2)}
                  </pre>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function MemoryIndexPanel() {
  const [items, setItems] = useState<MemoryItemEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [sourceType, setSourceType] = useState('');
  const [entityType, setEntityType] = useState('');
  const [visibility, setVisibility] = useState('');
  const [reindexing, setReindexing] = useState(false);

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: '100' });
      if (query.trim()) params.set('query', query.trim());
      if (sourceType) params.set('source_type', sourceType);
      if (entityType) params.set('entity_type', entityType);
      if (visibility) params.set('visibility', visibility);
      const res = await authFetch(`/dev/memory-items?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setItems(await res.json());
    } catch (err) {
      toast.error(`Failed to load memory items: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [entityType, query, sourceType, visibility]);

  useEffect(() => { loadItems(); }, [loadItems]);

  const handleReindex = async (resetIndex: boolean) => {
    setReindexing(true);
    try {
      const res = await authFetch(`/dev/reindex-memory?reset_index=${resetIndex ? 'true' : 'false'}`, {
        method: 'POST',
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      toast.success(`Synced ${data.count} memory items`);
      await loadItems();
    } catch (err) {
      toast.error(`Reindex failed: ${(err as Error).message}`);
    } finally {
      setReindexing(false);
    }
  };

  return (
    <Card className="p-4 rounded-xl space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold text-sm">Memory Index</h2>
          <p className="text-xs text-muted-foreground mt-1">Normalized memory items stored for ranking and retrieval.</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={reindexing} onClick={() => handleReindex(false)}>
            {reindexing ? 'Syncing…' : 'Sync'}
          </Button>
          <Button variant="outline" size="sm" disabled={reindexing} onClick={() => handleReindex(true)}>
            {reindexing ? 'Resetting…' : 'Reset + Sync'}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
        <Textarea
          className="md:col-span-2 min-h-[44px]"
          rows={1}
          placeholder="Filter by title/content…"
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
        <Select value={sourceType || 'all'} onValueChange={v => setSourceType(v === 'all' ? '' : v)}>
          <SelectTrigger><SelectValue placeholder="Source type" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All sources</SelectItem>
            <SelectItem value="property">Property</SelectItem>
            <SelectItem value="unit">Unit</SelectItem>
            <SelectItem value="tenant">Tenant</SelectItem>
            <SelectItem value="vendor">Vendor</SelectItem>
            <SelectItem value="lease">Lease</SelectItem>
            <SelectItem value="task">Task</SelectItem>
            <SelectItem value="entity_note">Entity note</SelectItem>
            <SelectItem value="agent_memory">General note</SelectItem>
            <SelectItem value="conversation">Conversation</SelectItem>
            <SelectItem value="document">Document</SelectItem>
          </SelectContent>
        </Select>
        <Select value={`${entityType || 'all'}:${visibility || 'all'}`} onValueChange={v => {
          const [nextEntityType, nextVisibility] = v.split(':');
          setEntityType(nextEntityType === 'all' ? '' : nextEntityType);
          setVisibility(nextVisibility === 'all' ? '' : nextVisibility);
        }}>
          <SelectTrigger><SelectValue placeholder="Entity / visibility" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all:all">All entities / visibility</SelectItem>
            <SelectItem value="property:shared">Property / shared</SelectItem>
            <SelectItem value="unit:shared">Unit / shared</SelectItem>
            <SelectItem value="tenant:shared">Tenant / shared</SelectItem>
            <SelectItem value="vendor:shared">Vendor / shared</SelectItem>
            <SelectItem value="general:private">General / private</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="max-h-[420px] overflow-y-auto space-y-2">
        {loading && <p className="text-sm text-muted-foreground py-4">Loading memory items…</p>}
        {!loading && items.length === 0 && <p className="text-sm text-muted-foreground py-4">No memory items matched the current filters.</p>}
        {items.map(item => (
          <div key={item.id} className="border rounded-lg p-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline" className="text-[10px]">{item.source_type}</Badge>
              <Badge variant="outline" className="text-[10px]">{item.entity_type}</Badge>
              <Badge className={cn("text-[10px]", item.visibility === 'private' ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-700')}>
                {item.visibility}
              </Badge>
              <span className="text-xs font-medium truncate">{item.title || `${item.source_type}:${item.source_id}`}</span>
            </div>
            <p className="text-xs text-muted-foreground whitespace-pre-wrap">{item.content}</p>
            <div className="flex flex-wrap gap-3 text-[10px] text-muted-foreground">
              <span>Entity: {item.entity_id}</span>
              <span>Source ID: {item.source_id}</span>
              {item.updated_at && <span>Updated: {new Date(item.updated_at).toLocaleString()}</span>}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function RetrievalDebugger() {
  const [query, setQuery] = useState('');
  const [intent, setIntent] = useState('answer_question');
  const [surface, setSurface] = useState('dev');
  const [taskId, setTaskId] = useState('');
  const [propertyId, setPropertyId] = useState('');
  const [unitId, setUnitId] = useState('');
  const [tenantId, setTenantId] = useState('');
  const [vendorId, setVendorId] = useState('');
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<RankedMemoryEntry[]>([]);

  const handleRun = async () => {
    setLoading(true);
    try {
      const res = await authFetch('/dev/retrieve-context', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query,
          intent,
          surface,
          task_id: taskId || null,
          property_id: propertyId || null,
          unit_id: unitId || null,
          tenant_id: tenantId || null,
          vendor_id: vendorId || null,
          limit: 10,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setItems(data.items ?? []);
    } catch (err) {
      toast.error(`Retrieval failed: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card className="p-4 rounded-xl space-y-3">
      <div>
        <h2 className="font-semibold text-sm">Retrieval Playground</h2>
        <p className="text-xs text-muted-foreground mt-1">Run the hybrid retriever with live ranking signals and inspect why items won.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
        <Textarea
          className="md:col-span-2 min-h-[72px]"
          rows={2}
          placeholder="Ask a domain-specific question or draft goal…"
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
        <Select value={intent} onValueChange={setIntent}>
          <SelectTrigger><SelectValue placeholder="Intent" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="answer_question">answer_question</SelectItem>
            <SelectItem value="draft_message">draft_message</SelectItem>
            <SelectItem value="triage">triage</SelectItem>
            <SelectItem value="follow_up">follow_up</SelectItem>
            <SelectItem value="summarize">summarize</SelectItem>
            <SelectItem value="task_context">task_context</SelectItem>
          </SelectContent>
        </Select>
        <Select value={surface} onValueChange={setSurface}>
          <SelectTrigger><SelectValue placeholder="Surface" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="dev">dev</SelectItem>
            <SelectItem value="chat">chat</SelectItem>
            <SelectItem value="task">task</SelectItem>
            <SelectItem value="scheduler">scheduler</SelectItem>
            <SelectItem value="eval">eval</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-5 gap-2">
        <Textarea rows={1} className="min-h-[44px]" placeholder="Task ID" value={taskId} onChange={e => setTaskId(e.target.value)} />
        <Textarea rows={1} className="min-h-[44px]" placeholder="Property ID" value={propertyId} onChange={e => setPropertyId(e.target.value)} />
        <Textarea rows={1} className="min-h-[44px]" placeholder="Unit ID" value={unitId} onChange={e => setUnitId(e.target.value)} />
        <Textarea rows={1} className="min-h-[44px]" placeholder="Tenant ID" value={tenantId} onChange={e => setTenantId(e.target.value)} />
        <Textarea rows={1} className="min-h-[44px]" placeholder="Vendor ID" value={vendorId} onChange={e => setVendorId(e.target.value)} />
      </div>

      <div className="flex justify-end">
        <Button onClick={handleRun} disabled={loading || !query.trim()}>
          {loading ? 'Ranking…' : 'Run Retrieval'}
        </Button>
      </div>

      <div className="max-h-[420px] overflow-y-auto space-y-2">
        {!loading && items.length === 0 && (
          <p className="text-sm text-muted-foreground py-4">No ranked results yet. Run a retrieval query to inspect the scoring output.</p>
        )}
        {items.map((item, index) => (
          <div key={item.memory_item_id} className="border rounded-lg p-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge className="bg-slate-900 text-white text-[10px]">#{index + 1}</Badge>
              <Badge variant="outline" className="text-[10px]">{item.source_type}</Badge>
              <Badge variant="outline" className="text-[10px]">{item.entity_type}</Badge>
              <span className="text-xs font-medium">{item.title || `${item.source_type}:${item.source_id}`}</span>
            </div>
            <div className="grid grid-cols-3 gap-2 text-[10px] text-muted-foreground">
              <span>Final: {item.final_score.toFixed(2)}</span>
              <span>Vector: {item.vector_score.toFixed(2)}</span>
              <span>Heuristic: {item.heuristic_score.toFixed(2)}</span>
            </div>
            <p className="text-xs whitespace-pre-wrap">{item.content}</p>
            {item.reasons.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {item.reasons.map(reason => (
                  <Badge key={reason} variant="outline" className="text-[10px]">{reason}</Badge>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

async function fetchDevHistory(tenantId: string): Promise<{ taskId: string | null; messages: ChatEntry[] }> {
  const res = await authFetch(`/dev/history/${tenantId}`);
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
      const res = await authFetch('/dev/simulate-inbound', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <RetrievalDebugger />
        <MemoryIndexPanel />
      </div>

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
