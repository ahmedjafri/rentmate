import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
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
import { graphqlQuery } from '@/data/api';
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
  createdAt: string;
}

const QUERY = `
  query($uid: String!) {
    scheduledTask(uid: $uid) {
      uid name prompt schedule scheduleDisplay isDefault enabled state
      repeat completedCount nextRunAt lastRunAt lastStatus lastOutput createdAt
    }
  }
`;

const UPDATE = `
  mutation($uid: String!, $name: String, $prompt: String, $schedule: String, $enabled: Boolean) {
    updateScheduledTask(uid: $uid, name: $name, prompt: $prompt, schedule: $schedule, enabled: $enabled) { uid }
  }
`;

const DELETE = `mutation($uid: String!) { deleteScheduledTask(uid: $uid) }`;

const RUN = `
  mutation($uid: String!) {
    runScheduledTask(uid: $uid) {
      uid lastStatus lastOutput lastRunAt
    }
  }
`;

const SIMULATE = `
  mutation($uid: String!) { simulateScheduledTask(uid: $uid) }
`;

const ScheduledTaskDetail = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [task, setTask] = useState<ScheduledTask | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [simulating, setSimulating] = useState(false);
  const [simOutput, setSimOutput] = useState<string | null>(null);

  // Edit form
  const [editName, setEditName] = useState('');
  const [editPrompt, setEditPrompt] = useState('');
  const [editSchedule, setEditSchedule] = useState('');

  const fetchTask = async () => {
    if (!id) return;
    try {
      const data = await graphqlQuery<{ scheduledTask: ScheduledTask | null }>(QUERY, { uid: id });
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
      await graphqlQuery(UPDATE, {
        uid: task.uid,
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
      await graphqlQuery(UPDATE, { uid: task.uid, enabled: !task.enabled });
      toast.success(task.enabled ? 'Paused' : 'Resumed');
      fetchTask();
    } catch { toast.error('Failed'); }
  };

  const handleDelete = async () => {
    if (!task || task.isDefault) return;
    if (!confirm(`Delete "${task.name}"?`)) return;
    try {
      await graphqlQuery(DELETE, { uid: task.uid });
      toast.success('Deleted');
      navigate('/scheduled-tasks');
    } catch { toast.error('Failed'); }
  };

  const handleRun = async () => {
    if (!task) return;
    setRunning(true);
    try {
      const data = await graphqlQuery<{ runScheduledTask: Partial<ScheduledTask> }>(RUN, { uid: task.uid });
      const result = data.runScheduledTask;
      setTask(prev => prev ? { ...prev, ...result } as ScheduledTask : prev);
      toast.success(result.lastStatus === 'ok' ? 'Run completed' : 'Run failed');
    } catch (e) { toast.error(e instanceof Error ? e.message : 'Run failed'); }
    finally { setRunning(false); }
  };

  const handleSimulate = async () => {
    if (!task) return;
    setSimulating(true);
    setSimOutput(null);
    try {
      const data = await graphqlQuery<{ simulateScheduledTask: string }>(SIMULATE, { uid: task.uid });
      setSimOutput(data.simulateScheduledTask);
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
          <Button variant="outline" size="sm" onClick={handleToggle} className="gap-1.5">
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
            Simulate
          </Button>
        </div>

        {/* Simulation output */}
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
