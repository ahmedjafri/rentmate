import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { authFetch } from '@/lib/auth';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  ArrowLeft, Zap, Play, Pause, Trash2, Clock, CheckCircle2, XCircle,
  Loader2, Save, FlaskConical,
} from 'lucide-react';
import { deleteScheduledTask, getScheduledTask, updateScheduledTask } from '@/graphql/client';
import { toast } from 'sonner';
import { formatDistanceToNow } from 'date-fns';
import { PageLoader } from '@/components/ui/page-loader';
import { cn } from '@/lib/utils';

interface ScheduledTask {
  uid: string;
  name: string;
  prompt: string;
  schedule: string;
  scheduleDisplay: string | null;
  isDefault: boolean;
  enabled: boolean;
  state: string;
  repeat: number | null;
  completedCount: number;
  nextRunAt: string | null;
  lastRunAt: string | null;
  lastStatus: string | null;
  lastOutput: string | null;
  simulatedAt: string | null;
  createdAt: string;
}

interface SimulatedSuggestion {
  id: string;
  title: string;
  body: string;
  category?: string | null;
  urgency?: string | null;
  property_id?: string | null;
  task_id?: string | null;
  risk_score?: number | null;
  action_payload?: Record<string, unknown> | null;
}

const ScheduledTaskDetail = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [task, setTask] = useState<ScheduledTask | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [runTrace, setRunTrace] = useState<string[]>([]);
  const [runOutput, setRunOutput] = useState<string | null>(null);
  const [simulating, setSimulating] = useState(false);
  const [simTrace, setSimTrace] = useState<string[]>([]);
  const [simOutput, setSimOutput] = useState<string | null>(null);
  const [simSuggestions, setSimSuggestions] = useState<SimulatedSuggestion[]>([]);
  const runScrollRef = useRef<HTMLPreElement>(null);
  const simScrollRef = useRef<HTMLPreElement>(null);

  // Edit form
  const [editName, setEditName] = useState('');
  const [editPrompt, setEditPrompt] = useState('');
  const [editSchedule, setEditSchedule] = useState('');

  const fetchTask = async () => {
    if (!id) return;
    try {
      const data = await getScheduledTask(id);
      const st = data.scheduledTask;
      setTask(st);
      if (st) {
        setEditName(st.name);
        setEditPrompt(st.prompt);
        setEditSchedule(st.schedule);
      }
    } catch { /* ignore */ } finally { setLoading(false); }
  };

  useEffect(() => { fetchTask(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSave = async () => {
    if (!task || task.isDefault) return;
    setSaving(true);
    try {
      await updateScheduledTask(task.uid, {
        name: editName || undefined,
        prompt: editPrompt || undefined,
        schedule: editSchedule || undefined,
      });
      toast.success('Saved');
      fetchTask();
    } catch (e) { toast.error(e instanceof Error ? e.message : 'Failed'); }
    finally { setSaving(false); }
  };

  const handleToggle = async () => {
    if (!task) return;
    try {
      await updateScheduledTask(task.uid, { enabled: !task.enabled });
      toast.success(task.enabled ? 'Paused' : 'Resumed');
      fetchTask();
    } catch { toast.error('Failed'); }
  };

  const handleDelete = async () => {
    if (!task || task.isDefault) return;
    if (!confirm(`Delete "${task.name}"?`)) return;
    try {
      await deleteScheduledTask(task.uid);
      toast.success('Deleted');
      navigate('/scheduled-tasks');
    } catch { toast.error('Failed'); }
  };

  const handleRun = async () => {
    if (!task) return;
    setRunning(true);
    setRunTrace([]);
    setRunOutput(null);
    try {
      const res = await authFetch(`/api/scheduled-task/${task.uid}/run`, { method: 'POST' });
      if (!res.ok) {
        setRunOutput(`Error: HTTP ${res.status}`);
        return;
      }
      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const event = JSON.parse(line.slice(6));
            if (event.type === 'progress') {
              setRunTrace(prev => [...prev, event.text]);
              runScrollRef.current?.scrollTo(0, runScrollRef.current.scrollHeight);
            } else if (event.type === 'done') {
              setRunOutput(event.reply);
              if (event.task) {
                setTask(prev => prev ? {
                  ...prev,
                  lastStatus: event.task.lastStatus ?? prev.lastStatus,
                  lastOutput: event.task.lastOutput ?? prev.lastOutput,
                  lastRunAt: event.task.lastRunAt ?? prev.lastRunAt,
                  completedCount: event.task.completedCount ?? prev.completedCount,
                  nextRunAt: event.task.nextRunAt ?? prev.nextRunAt,
                  state: event.task.state ?? prev.state,
                  enabled: event.task.enabled ?? prev.enabled,
                } as ScheduledTask : prev);
              }
            } else if (event.type === 'error') {
              setRunOutput(`Error: ${event.message}`);
            }
          } catch { /* skip malformed */ }
        }
      }
      fetchTask();
      toast.success('Run completed');
    } catch (e) { setRunOutput(`Error: ${e instanceof Error ? e.message : 'Run failed'}`); }
    finally { setRunning(false); }
  };

  const handleSimulate = async () => {
    if (!task) return;
    setSimulating(true);
    setSimTrace([]);
    setSimOutput(null);
    setSimSuggestions([]);
    try {
      const res = await authFetch(`/api/scheduled-task/${task.uid}/simulate`, { method: 'POST' });
      if (!res.ok) {
        setSimOutput(`Error: HTTP ${res.status}`);
        return;
      }
      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const event = JSON.parse(line.slice(6));
            if (event.type === 'progress') {
              setSimTrace(prev => [...prev, event.text]);
              simScrollRef.current?.scrollTo(0, simScrollRef.current.scrollHeight);
            } else if (event.type === 'done') {
              setSimOutput(event.reply);
              setSimSuggestions(Array.isArray(event.suggestions) ? event.suggestions : []);
            } else if (event.type === 'error') {
              setSimOutput(`Error: ${event.message}`);
              setSimSuggestions([]);
            }
          } catch { /* skip malformed */ }
        }
      }
      fetchTask(); // Refresh to get simulatedAt
    } catch (e) { setSimOutput(`Error: ${e instanceof Error ? e.message : 'Failed'}`); }
    finally { setSimulating(false); }
  };

  if (loading) return <PageLoader />;
  if (!task) {
    return (
      <div className="p-6 text-center text-muted-foreground">
        <p>Scheduled task not found</p>
        <Button variant="ghost" className="mt-4" onClick={() => navigate('/scheduled-tasks')}>
          <ArrowLeft className="h-4 w-4 mr-2" /> Back
        </Button>
      </div>
    );
  }

  const editable = !task.isDefault;
  const hasChanges = editName !== task.name || editPrompt !== task.prompt || editSchedule !== task.schedule;

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate('/scheduled-tasks')} className="shrink-0">
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Zap className={cn("h-5 w-5 shrink-0", task.enabled ? "text-primary" : "text-muted-foreground")} />
            <h1 className="text-xl font-bold truncate">{task.name}</h1>
          </div>
          <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {task.scheduleDisplay || task.schedule}
            </span>
            {task.isDefault && <Badge variant="secondary" className="text-[10px]">System default</Badge>}
            {!task.enabled && <Badge variant="secondary" className="text-[10px]">Paused</Badge>}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <Button
            variant="outline"
            size="sm"
            onClick={handleToggle}
            disabled={!task.enabled && !task.simulatedAt}
            title={!task.enabled && !task.simulatedAt ? 'Run a simulation first before enabling' : undefined}
            className="gap-1.5"
          >
            {task.enabled ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            {task.enabled ? 'Pause' : 'Resume'}
          </Button>
          {editable && (
            <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive/60 hover:text-destructive" onClick={handleDelete}>
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {/* Edit form (only for non-default tasks) */}
      <Card className="p-5 rounded-xl space-y-4">
        <div className="space-y-2">
          <Label>Name</Label>
          <Input value={editName} onChange={e => setEditName(e.target.value)} disabled={!editable} />
        </div>
        <div className="space-y-2">
          <Label>Prompt — what the agent does each run</Label>
          <Textarea
            value={editPrompt}
            onChange={e => setEditPrompt(e.target.value)}
            disabled={!editable}
            className="min-h-[120px] font-mono text-sm"
          />
        </div>
        <div className="space-y-2">
          <Label>Schedule</Label>
          <Input value={editSchedule} onChange={e => setEditSchedule(e.target.value)} disabled={!editable} />
          <p className="text-[10px] text-muted-foreground">
            Cron expression or named: weekly, daily, every 4h, monthly
          </p>
        </div>
        {editable && hasChanges && (
          <Button onClick={handleSave} disabled={saving} className="gap-1.5">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save Changes
          </Button>
        )}
        {!editable && (
          <p className="text-xs text-muted-foreground italic">
            System default tasks cannot be edited. You can pause/resume or create a custom task with different settings.
          </p>
        )}
      </Card>

      {/* Run / Simulate */}
      <Card className="p-5 rounded-xl space-y-4">
        <h2 className="text-sm font-semibold">Run</h2>
        <div className="flex items-center gap-3">
          <Button onClick={handleRun} disabled={running || simulating} variant="default" className="gap-1.5">
            {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            Run Now
          </Button>
          <Button onClick={handleSimulate} disabled={running || simulating} variant="outline" className="gap-1.5">
            {simulating ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}
            {task.simulatedAt ? 'Re-simulate' : 'Simulate'}
          </Button>
          {!task.simulatedAt && !task.enabled && (
            <span className="text-xs text-amber-600 dark:text-amber-400">
              Simulation required before enabling
            </span>
          )}
        </div>

        {/* Simulation trace + output */}
        {(runTrace.length > 0 || runOutput !== null || simTrace.length > 0 || simOutput !== null || simSuggestions.length > 0) && (
          <div className="space-y-2">
            {runTrace.length > 0 && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-1.5">
                  <Play className="h-3.5 w-3.5 text-primary" />
                  <span className="text-xs font-semibold text-primary">
                    {running ? 'Run Reasoning…' : 'Run Trace'}
                  </span>
                </div>
                <pre
                  ref={runScrollRef}
                  className="rounded-lg bg-muted/50 border p-3 text-[11px] whitespace-pre-wrap font-mono max-h-48 overflow-y-auto space-y-0.5"
                >
                  {runTrace.map((line, i) => (
                    <div key={i} className={cn(
                      "py-0.5",
                      i === runTrace.length - 1 && running ? "text-foreground/80" : "text-muted-foreground/60"
                    )}>
                      {line}
                    </div>
                  ))}
                </pre>
              </div>
            )}

            {runOutput !== null && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-1.5">
                  <Play className="h-3.5 w-3.5 text-primary" />
                  <span className="text-xs font-semibold text-primary">Run Result</span>
                </div>
                <pre className="rounded-lg bg-primary/5 border border-primary/20 p-3 text-xs whitespace-pre-wrap font-mono max-h-80 overflow-y-auto">
                  {runOutput}
                </pre>
              </div>
            )}

            {/* Live trace */}
            {simTrace.length > 0 && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-1.5">
                  <FlaskConical className="h-3.5 w-3.5 text-violet-500" />
                  <span className="text-xs font-semibold text-violet-600 dark:text-violet-400">
                    {simulating ? 'Agent Reasoning…' : 'Reasoning Trace'}
                  </span>
                </div>
                <pre
                  ref={simScrollRef}
                  className="rounded-lg bg-muted/50 border p-3 text-[11px] whitespace-pre-wrap font-mono max-h-48 overflow-y-auto space-y-0.5"
                >
                  {simTrace.map((line, i) => (
                    <div key={i} className={cn(
                      "py-0.5",
                      i === simTrace.length - 1 && simulating ? "text-foreground/80" : "text-muted-foreground/60"
                    )}>
                      {line}
                    </div>
                  ))}
                </pre>
              </div>
            )}

            {/* Final result */}
            {simOutput !== null && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-1.5">
                  <FlaskConical className="h-3.5 w-3.5 text-violet-500" />
                  <span className="text-xs font-semibold text-violet-600 dark:text-violet-400">Simulation Result</span>
                </div>
                <pre className="rounded-lg bg-violet-50 dark:bg-violet-950/30 border border-violet-200 dark:border-violet-800 p-3 text-xs whitespace-pre-wrap font-mono max-h-80 overflow-y-auto">
                  {simOutput}
                </pre>
              </div>
            )}

            {simSuggestions.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center gap-1.5">
                  <FlaskConical className="h-3.5 w-3.5 text-violet-500" />
                  <span className="text-xs font-semibold text-violet-600 dark:text-violet-400">
                    Suggestions That Would Be Created
                  </span>
                </div>
                <div className="space-y-3">
                  {simSuggestions.map((suggestion, index) => (
                    <Card
                      key={suggestion.id || `${suggestion.title}-${index}`}
                      className="rounded-xl border-violet-200 bg-violet-50/70 p-4 dark:border-violet-900 dark:bg-violet-950/20"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="space-y-1">
                          <h3 className="text-sm font-semibold text-foreground">{suggestion.title}</h3>
                          <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                            {suggestion.category && <Badge variant="secondary">{suggestion.category}</Badge>}
                            {suggestion.urgency && <Badge variant="outline">{suggestion.urgency}</Badge>}
                            {suggestion.property_id && <span>Property: {suggestion.property_id}</span>}
                            {suggestion.task_id && <span>Task: {suggestion.task_id}</span>}
                            {suggestion.risk_score !== null && suggestion.risk_score !== undefined && (
                              <span>Risk: {suggestion.risk_score}</span>
                            )}
                          </div>
                        </div>
                      </div>
                      {suggestion.body && (
                        <p className="mt-3 whitespace-pre-wrap text-sm text-foreground/90">{suggestion.body}</p>
                      )}
                      {suggestion.action_payload && Object.keys(suggestion.action_payload).length > 0 && (
                        <div className="mt-3 space-y-1.5">
                          <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                            Action Payload
                          </div>
                          <pre className="overflow-x-auto rounded-lg border bg-background/80 p-3 text-[11px] whitespace-pre-wrap font-mono">
                            {JSON.stringify(suggestion.action_payload, null, 2)}
                          </pre>
                        </div>
                      )}
                    </Card>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Last run output */}
        {task.lastOutput && (
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              {task.lastStatus === 'ok'
                ? <CheckCircle2 className="h-3.5 w-3.5 text-accent" />
                : <XCircle className="h-3.5 w-3.5 text-destructive" />}
              <span className="text-xs font-semibold text-muted-foreground">
                Last run {task.lastRunAt ? formatDistanceToNow(new Date(task.lastRunAt), { addSuffix: true }) : ''}
              </span>
            </div>
            <pre className="rounded-lg bg-muted/40 border p-3 text-xs whitespace-pre-wrap font-mono max-h-60 overflow-y-auto">
              {task.lastOutput}
            </pre>
          </div>
        )}

        {/* Stats */}
        <div className="flex items-center gap-6 text-xs text-muted-foreground pt-2 border-t">
          <span>{task.completedCount} run{task.completedCount !== 1 ? 's' : ''} total</span>
          {task.nextRunAt && task.enabled && (
            <span>Next: {formatDistanceToNow(new Date(task.nextRunAt), { addSuffix: true })}</span>
          )}
          {task.repeat && <span>Repeats: {task.repeat - task.completedCount} remaining</span>}
          <span>Created: {new Date(task.createdAt).toLocaleDateString()}</span>
        </div>
      </Card>
    </div>
  );
};

export default ScheduledTaskDetail;
