import { useState, useEffect, useCallback } from 'react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
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
import { Terminal, Trash2, Activity, ChevronDown, ChevronRight, RefreshCw, Copy } from 'lucide-react';
import { cn } from '@/lib/utils';
import { authFetch } from '@/lib/auth';
import { toast } from 'sonner';

interface TraceEntry {
  id: string;
  timestamp: string;
  trace_type: string;
  source: string;
  run_id: string | null;
  sequence_num: number | null;
  task_id: string | null;
  conversation_id: string | null;
  tool_name: string | null;
  summary: string;
  detail: string | null;
  suggestion_id: string | null;
}

type RunStatus = 'running' | 'completed' | 'errored' | 'iteration_limit' | 'aborted';

interface RunEntry {
  id: string;
  source: string;
  status: RunStatus;
  task_id: string | null;
  conversation_id: string | null;
  model: string | null;
  agent_version: string;
  execution_path: string;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  iteration_count: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_cents: string;
  trigger_input: string | null;
  final_response: string | null;
  error_message: string | null;
  trace_count: number;
  // ATIF Step rows for post-cutover runs. Legacy runs (pre-cutover) only
  // have ``trace_count``; when ``step_count > 0`` we show steps instead.
  step_count: number;
}

// Harbor ATIF v1.4 Step shape returned by /dev/runs/{id}/trajectory.
interface AtifToolCall {
  tool_call_id: string;
  function_name: string;
  arguments: Record<string, unknown>;
}

interface AtifObservationResult {
  source_call_id: string;
  content: string;
}

interface AtifStep {
  step_id: number;
  timestamp: string | null;
  source: 'user' | 'agent' | 'system';
  message: string;
  model_name?: string | null;
  reasoning_content?: string | null;
  tool_calls?: AtifToolCall[];
  observation?: { results: AtifObservationResult[] };
  metrics?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    cached_tokens?: number;
    cost_usd?: number;
  };
  extra?: Record<string, unknown>;
}

interface AtifTrajectory {
  schema_version: string;
  session_id: string;
  agent: {
    name: string;
    version: string | null;
    model_name: string | null;
  };
  steps: AtifStep[];
  final_metrics: {
    total_prompt_tokens: number;
    total_completion_tokens: number;
    total_cached_tokens?: number;
    total_cost_usd: number;
    total_steps: number;
  };
  extra?: Record<string, unknown>;
}

const STEP_SOURCE_COLORS: Record<AtifStep['source'], string> = {
  user: 'bg-slate-100 text-slate-800',
  agent: 'bg-purple-100 text-purple-800',
  system: 'bg-amber-100 text-amber-800',
};

interface TraceFilterOption {
  id: string;
  raw_id?: string;
  scope?: 'task' | 'routine';
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

const RUN_STATUS_COLORS: Record<RunStatus, string> = {
  completed: 'bg-slate-100 text-slate-800',
  running: 'bg-blue-100 text-blue-800',
  errored: 'bg-red-100 text-red-800',
  iteration_limit: 'bg-amber-100 text-amber-800',
  aborted: 'bg-gray-100 text-gray-700',
};

function RunsPanel() {
  const [runs, setRuns] = useState<RunEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [sourceFilter, setSourceFilter] = useState<string>('all');
  const [taskFilter, setTaskFilter] = useState<string>('all');
  const [conversationFilter, setConversationFilter] = useState<string>('all');
  const [taskOptions, setTaskOptions] = useState<TraceFilterOption[]>([]);
  const [chatOptions, setChatOptions] = useState<TraceFilterOption[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [tracesByRun, setTracesByRun] = useState<Map<string, TraceEntry[]>>(new Map());
  const [traceLoading, setTraceLoading] = useState<Set<string>>(new Set());
  // ATIF trajectory cache. Always populated for expanded runs (the
  // legacy adapter on the server synthesizes ATIF steps from
  // pre-cutover trace rows so the UI has a single rendering path).
  const [trajectoryByRun, setTrajectoryByRun] = useState<Map<string, AtifTrajectory>>(new Map());
  const [trajectoryLoading, setTrajectoryLoading] = useState<Set<string>>(new Set());
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const [selectedTrace, setSelectedTrace] = useState<TraceDetailEntry | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailSection, setDetailSection] = useState<'overview' | 'context' | 'retrieval' | 'io' | 'reasoning' | 'raw'>('overview');

  const loadRuns = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: '50' });
      const selectedTaskOption = taskOptions.find(option => option.id === taskFilter);
      if (statusFilter !== 'all') params.set('status', statusFilter);
      if (sourceFilter !== 'all') params.set('source', sourceFilter);
      if (taskFilter !== 'all') {
        params.set('task_id', selectedTaskOption?.raw_id ?? taskFilter);
      }
      if (conversationFilter !== 'all') params.set('conversation_id', conversationFilter);
      const res = await authFetch(`/dev/runs?${params}`);
      if (res.ok) setRuns(await res.json());
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, [conversationFilter, sourceFilter, statusFilter, taskFilter, taskOptions]);

  const loadTracesForRun = useCallback(async (runId: string) => {
    setTraceLoading(prev => {
      const next = new Set(prev);
      next.add(runId);
      return next;
    });
    try {
      const res = await authFetch(`/dev/traces?run_id=${encodeURIComponent(runId)}&limit=500`);
      if (res.ok) {
        const data = (await res.json()) as TraceEntry[];
        setTracesByRun(prev => {
          const next = new Map(prev);
          next.set(runId, data);
          return next;
        });
      }
    } catch { /* ignore */ } finally {
      setTraceLoading(prev => {
        const next = new Set(prev);
        next.delete(runId);
        return next;
      });
    }
  }, []);

  const loadTrajectoryForRun = useCallback(async (runId: string) => {
    setTrajectoryLoading(prev => {
      const next = new Set(prev);
      next.add(runId);
      return next;
    });
    try {
      const res = await authFetch(`/dev/runs/${encodeURIComponent(runId)}/trajectory`);
      if (res.ok) {
        const data = (await res.json()) as AtifTrajectory;
        setTrajectoryByRun(prev => {
          const next = new Map(prev);
          next.set(runId, data);
          return next;
        });
      }
    } catch { /* ignore */ } finally {
      setTrajectoryLoading(prev => {
        const next = new Set(prev);
        next.delete(runId);
        return next;
      });
    }
  }, []);

  useEffect(() => { loadRuns(); }, [loadRuns]);

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

  // Auto-refresh every 5s — re-fetch the run list AND any expanded
  // run's trajectory (or legacy traces, for the run-detail dialog
  // path).
  useEffect(() => {
    const id = setInterval(() => {
      void loadRuns();
      expanded.forEach(runId => {
        void loadTrajectoryForRun(runId);
        if (tracesByRun.has(runId)) void loadTracesForRun(runId);
      });
    }, 5000);
    return () => clearInterval(id);
  }, [loadRuns, loadTracesForRun, loadTrajectoryForRun, expanded, tracesByRun]);

  const toggleRun = (runId: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(runId)) {
        next.delete(runId);
      } else {
        next.add(runId);
        if (!trajectoryByRun.has(runId)) {
          void loadTrajectoryForRun(runId);
        }
      }
      return next;
    });
  };

  const toggleStep = (key: string) => {
    setExpandedSteps(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

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

  const formatRunBlock = (run: RunEntry, traces: TraceEntry[] | undefined): string[] => {
    const lines: string[] = [
      `### Run ${run.id}  ${run.status}  ${run.source}  (${run.iteration_count} turns, ${run.total_input_tokens}→${run.total_output_tokens} tok, $${(Number(run.total_cost_cents) / 100).toFixed(4)})`,
    ];
    if (run.trigger_input) lines.push(`  trigger: ${run.trigger_input}`);
    if (run.final_response) lines.push(`  reply:   ${run.final_response}`);
    if (run.error_message) lines.push(`  error:   ${run.error_message}`);
    (traces ?? []).forEach(t => {
      lines.push(`  [${formatTime(t.timestamp)}] #${t.sequence_num ?? '?'} ${t.trace_type} (${t.source}) ${t.summary}`);
      if (t.detail) {
        const detailText = parseDetail(t.detail);
        if (detailText) {
          for (const detailLine of detailText.split('\n')) {
            lines.push(`    ${detailLine}`);
          }
        }
      }
    });
    return lines;
  };

  const copyAllRuns = () => {
    const lines: string[] = [];
    runs.forEach(run => {
      lines.push(...formatRunBlock(run, tracesByRun.get(run.id)));
      lines.push('');
    });
    copyText(lines.join('\n'), `Copied ${runs.length} runs`);
  };

  const copyRun = async (run: RunEntry) => {
    let traces = tracesByRun.get(run.id);
    if (!traces) {
      try {
        const res = await authFetch(`/dev/traces?run_id=${encodeURIComponent(run.id)}&limit=500`);
        if (res.ok) {
          traces = (await res.json()) as TraceEntry[];
          setTracesByRun(prev => {
            const next = new Map(prev);
            next.set(run.id, traces!);
            return next;
          });
        }
      } catch {
        toast.error('Failed to load traces for run');
        return;
      }
    }
    const lines = formatRunBlock(run, traces);
    copyText(lines.join('\n'), `Copied run (${(traces ?? []).length} traces)`);
  };

  const copyTrace = (trace: TraceEntry) => {
    const text = `[${trace.timestamp}] ${trace.trace_type} (${trace.source}) ${trace.summary}${trace.detail ? '\n' + parseDetail(trace.detail) : ''}`;
    copyText(text, 'Copied trace');
  };

  const formatTime = (ts: string) => {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  const formatDuration = (ms: number | null) => {
    if (ms == null) return 'running';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  };

  const formatCost = (cents: string) => {
    const dollars = Number(cents) / 100;
    if (!Number.isFinite(dollars) || dollars === 0) return '$0';
    return `$${dollars.toFixed(4)}`;
  };

  const shortModel = (model: string | null) => {
    if (!model) return '';
    const tail = model.split('/').pop() ?? model;
    return tail.replace(/-\d{8}$/, '').replace(/^(claude-|anthropic\.)/, '');
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

  const sourceOptions = Array.from(new Set(runs.map(r => r.source).filter(Boolean))) as string[];
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
          <h2 className="font-semibold text-sm">Agent Runs</h2>
          <Badge variant="outline" className="text-[10px]">{runs.length}</Badge>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <select
            className="text-xs border rounded px-2 py-1 bg-background"
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            aria-label="Filter by status"
          >
            <option value="all">All statuses</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="errored">Errored</option>
            <option value="iteration_limit">Iteration limit</option>
            <option value="aborted">Aborted</option>
          </select>
          <select
            className="text-xs border rounded px-2 py-1 bg-background max-w-[180px]"
            value={taskFilter}
            onChange={e => setTaskFilter(e.target.value)}
            aria-label="Filter by task"
          >
            <option value="all">All tasks</option>
            {taskOptions.map(task => (
              <option key={task.id} value={task.id}>
                {task.scope === 'routine' ? `Routine: ${task.title || task.raw_id || task.id}` : task.title || task.raw_id || task.id}
              </option>
            ))}
          </select>
          <select
            className="text-xs border rounded px-2 py-1 bg-background max-w-[180px]"
            value={conversationFilter}
            onChange={e => setConversationFilter(e.target.value)}
            aria-label="Filter by chat"
          >
            <option value="all">All chats</option>
            {chatOptions.map(chat => (
              <option key={chat.id} value={chat.id}>{chat.subject || chat.id}</option>
            ))}
          </select>
          <select
            className="text-xs border rounded px-2 py-1 bg-background"
            value={sourceFilter}
            onChange={e => setSourceFilter(e.target.value)}
            aria-label="Filter by source"
          >
            <option value="all">All sources</option>
            {sourceOptions.map(source => (
              <option key={source} value={source}>{source}</option>
            ))}
          </select>
          <Button variant="ghost" size="sm" onClick={loadRuns} disabled={loading} className="h-7 w-7 p-0" aria-label="Refresh">
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          </Button>
          {runs.length > 0 && (
            <Button variant="ghost" size="sm" onClick={copyAllRuns} className="h-7 text-[10px] px-2">
              Copy All
            </Button>
          )}
        </div>
      </div>

      <div className="max-h-[600px] overflow-y-auto space-y-1.5">
        {runs.length === 0 && !loading && (
          <p className="text-sm text-muted-foreground text-center py-8">No agent runs yet. Activity will appear here.</p>
        )}
        {runs.map(run => {
          const isExpanded = expanded.has(run.id);
          const trajectory = trajectoryByRun.get(run.id);
          const isLoadingTrajectory = trajectoryLoading.has(run.id);
          return (
            <div key={run.id} className="border rounded-lg">
              <div className="flex items-start gap-1 px-1 py-1">
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left px-2 py-1 hover:bg-muted/50 transition-colors rounded-md"
                  onClick={() => toggleRun(run.id)}
                  aria-expanded={isExpanded}
                  aria-label={`Toggle run ${run.id}`}
                >
                  <div className="flex items-center gap-2 flex-wrap min-w-0">
                    {isExpanded
                      ? <ChevronDown className="h-3 w-3 text-muted-foreground shrink-0" />
                      : <ChevronRight className="h-3 w-3 text-muted-foreground shrink-0" />}
                    <Badge className={cn("text-[9px] h-4 px-1.5 shrink-0", RUN_STATUS_COLORS[run.status] ?? 'bg-gray-100 text-gray-700')}>
                      {run.status}
                    </Badge>
                    <Badge variant="outline" className="text-[9px] h-4 px-1.5 shrink-0">{run.source}</Badge>
                    <span className="text-[10px] text-muted-foreground font-mono shrink-0">{formatTime(run.started_at)}</span>
                    <span className="text-[10px] text-muted-foreground shrink-0">{formatDuration(run.duration_ms)}</span>
                    <span className="text-[10px] text-muted-foreground shrink-0">{run.iteration_count} turns</span>
                    <span className="text-[10px] text-muted-foreground shrink-0 font-mono">
                      {run.total_input_tokens}→{run.total_output_tokens}
                    </span>
                    <span className="text-[10px] text-muted-foreground shrink-0">{formatCost(run.total_cost_cents)}</span>
                    {run.model && (
                      <Badge variant="outline" className="text-[9px] h-4 px-1.5 shrink-0">{shortModel(run.model)}</Badge>
                    )}
                    {run.task_id && (
                      <Badge variant="outline" className="text-[9px] h-4 px-1.5 shrink-0">task {run.task_id}</Badge>
                    )}
                    {run.conversation_id && (
                      <Badge variant="outline" className="text-[9px] h-4 px-1.5 shrink-0">chat</Badge>
                    )}
                    <span className="text-[10px] text-muted-foreground shrink-0">
                      {run.step_count > 0
                        ? `${run.step_count} steps`
                        : `${run.trace_count} traces`}
                    </span>
                  </div>
                  {(run.trigger_input || run.error_message) && (
                    <div className="mt-1 ml-5 text-xs text-muted-foreground truncate">
                      {run.error_message
                        ? <span className="text-red-700">{run.error_message}</span>
                        : run.trigger_input}
                    </div>
                  )}
                </button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 w-7 shrink-0 p-0"
                  aria-label={`Copy run ${run.id}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    void copyRun(run);
                  }}
                >
                  <Copy className="h-3.5 w-3.5" />
                </Button>
              </div>

              {isExpanded && (
                <div className="border-t bg-muted/20 px-1 py-1 space-y-1">
                  {isLoadingTrajectory && !trajectory && (
                    <p className="text-[10px] text-muted-foreground text-center py-2">Loading trajectory…</p>
                  )}
                  {trajectory && trajectory.steps.length === 0 && (
                    <p className="text-[10px] text-muted-foreground text-center py-2">No steps recorded for this run.</p>
                  )}
                  {trajectory?.steps.map(step => {
                    const stepKey = `${run.id}:${step.step_id}`;
                    const stepExpanded = expandedSteps.has(stepKey);
                    const toolCount = step.tool_calls?.length ?? 0;
                    const obsCount = step.observation?.results?.length ?? 0;
                    const errored = (step.extra as Record<string, unknown> | undefined)?.error_kind === 'tool_error'
                      || (step.extra as Record<string, unknown> | undefined)?.step_errored === true;
                    const messageOneLine = (step.message || '').split('\n', 1)[0] || '(no message)';
                    return (
                      <div key={stepKey} className="border rounded-md bg-background/40">
                        <button
                          type="button"
                          className="w-full text-left px-2 py-1 flex items-center gap-2 hover:bg-background transition-colors rounded-md"
                          onClick={() => toggleStep(stepKey)}
                          aria-expanded={stepExpanded}
                        >
                          {stepExpanded
                            ? <ChevronDown className="h-3 w-3 text-muted-foreground shrink-0" />
                            : <ChevronRight className="h-3 w-3 text-muted-foreground shrink-0" />}
                          <span className="text-[10px] text-muted-foreground font-mono w-8 shrink-0">
                            #{step.step_id}
                          </span>
                          <span className="text-[10px] text-muted-foreground font-mono w-16 shrink-0">
                            {step.timestamp ? formatTime(step.timestamp) : ''}
                          </span>
                          <Badge className={cn(
                            'text-[9px] h-4 px-1.5 shrink-0',
                            STEP_SOURCE_COLORS[step.source] ?? 'bg-gray-100 text-gray-700',
                          )}>
                            {step.source}
                          </Badge>
                          {step.model_name && (
                            <Badge variant="outline" className="text-[9px] h-4 px-1.5 shrink-0">
                              {shortModel(step.model_name)}
                            </Badge>
                          )}
                          {toolCount > 0 && (
                            <Badge variant="outline" className="text-[9px] h-4 px-1.5 shrink-0">
                              {toolCount} tool{toolCount === 1 ? '' : 's'}
                            </Badge>
                          )}
                          {errored && (
                            <Badge className="text-[9px] h-4 px-1.5 shrink-0 bg-red-100 text-red-800">
                              error
                            </Badge>
                          )}
                          <span className="text-xs truncate flex-1">{messageOneLine}</span>
                          {step.metrics?.cost_usd !== undefined && step.metrics.cost_usd > 0 && (
                            <span className="text-[10px] text-muted-foreground font-mono shrink-0">
                              ${step.metrics.cost_usd.toFixed(4)}
                            </span>
                          )}
                          {step.metrics && (
                            <span className="text-[10px] text-muted-foreground font-mono shrink-0">
                              {step.metrics.prompt_tokens ?? 0}→{step.metrics.completion_tokens ?? 0}
                            </span>
                          )}
                        </button>
                        {stepExpanded && (
                          <div className="border-t px-3 py-2 space-y-2 text-xs">
                            {step.message && (
                              <div>
                                <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
                                  Message
                                </div>
                                <pre className="bg-muted/40 rounded p-2 whitespace-pre-wrap font-mono text-[11px]">
                                  {step.message}
                                </pre>
                              </div>
                            )}
                            {step.reasoning_content && (
                              <div>
                                <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
                                  Reasoning
                                </div>
                                <pre className="bg-muted/40 rounded p-2 whitespace-pre-wrap font-mono text-[11px]">
                                  {step.reasoning_content}
                                </pre>
                              </div>
                            )}
                            {step.tool_calls?.map(tc => {
                              // Pair each tool call with its observation result by source_call_id.
                              const result = step.observation?.results.find(
                                r => r.source_call_id === tc.tool_call_id,
                              );
                              const isError = result?.content?.startsWith('ERROR:');
                              return (
                                <div key={tc.tool_call_id} className="border rounded">
                                  <div className="px-2 py-1 flex items-center gap-2 bg-muted/30">
                                    <Badge variant="outline" className="text-[9px] h-4 px-1.5">
                                      {tc.function_name}
                                    </Badge>
                                    <span className="text-[10px] text-muted-foreground font-mono truncate">
                                      {tc.tool_call_id}
                                    </span>
                                    {isError && (
                                      <Badge className="text-[9px] h-4 px-1.5 bg-red-100 text-red-800">
                                        error
                                      </Badge>
                                    )}
                                  </div>
                                  <div className="px-2 py-1 grid grid-cols-1 md:grid-cols-2 gap-2">
                                    <div>
                                      <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
                                        Arguments
                                      </div>
                                      <pre className="bg-muted/40 rounded p-1.5 whitespace-pre-wrap font-mono text-[10px]">
                                        {JSON.stringify(tc.arguments, null, 2)}
                                      </pre>
                                    </div>
                                    <div>
                                      <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
                                        Observation
                                      </div>
                                      <pre className={cn(
                                        'rounded p-1.5 whitespace-pre-wrap font-mono text-[10px]',
                                        isError ? 'bg-red-50 text-red-800' : 'bg-muted/40',
                                      )}>
                                        {result?.content ?? '(no result)'}
                                      </pre>
                                    </div>
                                  </div>
                                </div>
                              );
                            })}
                            {/* Observations without a matching tool_call (rare but possible from the legacy adapter). */}
                            {(step.observation?.results ?? [])
                              .filter(r => !(step.tool_calls ?? []).some(tc => tc.tool_call_id === r.source_call_id))
                              .map(r => (
                                <div key={r.source_call_id} className="border rounded">
                                  <div className="px-2 py-1 bg-muted/30 text-[10px] text-muted-foreground font-mono">
                                    {r.source_call_id}
                                  </div>
                                  <pre className="px-2 py-1 whitespace-pre-wrap font-mono text-[10px]">
                                    {r.content}
                                  </pre>
                                </div>
                              ))}
                            {step.metrics && (
                              <div className="text-[10px] text-muted-foreground">
                                tokens: {step.metrics.prompt_tokens ?? 0} prompt /
                                {' '}{step.metrics.completion_tokens ?? 0} completion
                                {step.metrics.cached_tokens
                                  ? ` / ${step.metrics.cached_tokens} cached`
                                  : ''}
                                {step.metrics.cost_usd
                                  ? ` · cost $${step.metrics.cost_usd.toFixed(6)}`
                                  : ''}
                              </div>
                            )}
                            {step.extra && Object.keys(step.extra).length > 0 && (
                              <details className="text-[10px]">
                                <summary className="cursor-pointer text-muted-foreground">
                                  extra
                                </summary>
                                <pre className="mt-1 bg-muted/40 rounded p-2 whitespace-pre-wrap font-mono">
                                  {JSON.stringify(step.extra, null, 2)}
                                </pre>
                              </details>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
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

const DevTools = () => {
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
          Trace inspection, memory debugging, and destructive wipes. To exercise the agent as a tenant or vendor,
          open the tenant/vendor portal directly.
        </p>
      </div>

      {/* Traces */}
      <RunsPanel />

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
