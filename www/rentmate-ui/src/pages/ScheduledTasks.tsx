import { useState, useEffect } from 'react';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Zap, Plus, Play, Pause, Trash2, Clock, CheckCircle2, XCircle, Loader2, ChevronDown, ChevronUp } from 'lucide-react';
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
  query { scheduledTasks { uid name prompt schedule scheduleDisplay enabled state repeat completedCount nextRunAt lastRunAt lastStatus lastOutput createdAt } }
`;

const CREATE = `
  mutation($name: String!, $prompt: String!, $schedule: String!, $repeat: Int) {
    createScheduledTask(name: $name, prompt: $prompt, schedule: $schedule, repeat: $repeat) { uid }
  }
`;

const UPDATE = `
  mutation($uid: String!, $name: String, $prompt: String, $schedule: String, $enabled: Boolean) {
    updateScheduledTask(uid: $uid, name: $name, prompt: $prompt, schedule: $schedule, enabled: $enabled) { uid }
  }
`;

const DELETE = `mutation($uid: String!) { deleteScheduledTask(uid: $uid) }`;

const ScheduledTasksPage = () => {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [form, setForm] = useState({ name: '', prompt: '', schedule: '' });

  const fetchTasks = async () => {
    try {
      const data = await graphqlQuery<{ scheduledTasks: ScheduledTask[] }>(QUERY);
      setTasks(data.scheduledTasks ?? []);
    } catch { /* ignore */ } finally { setLoading(false); }
  };

  useEffect(() => { fetchTasks(); }, []);

  const handleCreate = async () => {
    if (!form.name.trim() || !form.prompt.trim() || !form.schedule.trim()) return;
    try {
      await graphqlQuery(CREATE, { name: form.name, prompt: form.prompt, schedule: form.schedule });
      toast.success('Scheduled task created');
      setCreating(false);
      setForm({ name: '', prompt: '', schedule: '' });
      fetchTasks();
    } catch (e) { toast.error(e instanceof Error ? e.message : 'Failed'); }
  };

  const toggle = async (task: ScheduledTask) => {
    try {
      await graphqlQuery(UPDATE, { uid: task.uid, enabled: !task.enabled });
      toast.success(task.enabled ? 'Paused' : 'Resumed');
      fetchTasks();
    } catch { toast.error('Failed'); }
  };

  const remove = async (task: ScheduledTask) => {
    if (!confirm(`Delete "${task.name}"?`)) return;
    try {
      await graphqlQuery(DELETE, { uid: task.uid });
      setTasks(prev => prev.filter(t => t.uid !== task.uid));
      toast.success('Deleted');
    } catch { toast.error('Failed'); }
  };

  if (loading) return <PageLoader />;

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Scheduled Tasks</h1>
          <p className="text-sm text-muted-foreground">
            Recurring AI tasks that run on a schedule
          </p>
        </div>
        <Button onClick={() => setCreating(true)} className="gap-1.5 shrink-0">
          <Plus className="h-4 w-4" /> New Task
        </Button>
      </div>

      {tasks.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground">
          <Zap className="h-10 w-10 mx-auto mb-3 opacity-30" />
          <p className="text-sm font-medium">No scheduled tasks yet</p>
          <p className="text-xs mt-1">Create one to automate recurring property management work</p>
        </div>
      ) : (
        <div className="space-y-3">
          {tasks.map(task => (
            <Card key={task.uid} className="rounded-xl overflow-hidden">
              <div className="p-4 space-y-2">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <Zap className={cn("h-4 w-4 shrink-0", task.enabled ? "text-primary" : "text-muted-foreground")} />
                    <h3 className="font-medium text-sm truncate">{task.name}</h3>
                    {!task.enabled && <Badge variant="secondary" className="text-[10px] shrink-0">Paused</Badge>}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => toggle(task)} title={task.enabled ? 'Pause' : 'Resume'}>
                      {task.enabled ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive/60 hover:text-destructive" onClick={() => remove(task)} title="Delete">
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>

                <p className="text-xs text-muted-foreground line-clamp-2">{task.prompt}</p>

                <div className="flex items-center gap-4 text-[11px] text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {task.scheduleDisplay || task.schedule}
                  </span>
                  {task.nextRunAt && (
                    <span>Next: {formatDistanceToNow(new Date(task.nextRunAt), { addSuffix: true })}</span>
                  )}
                  {task.lastStatus && (
                    <span className="flex items-center gap-1">
                      {task.lastStatus === 'ok' ? <CheckCircle2 className="h-3 w-3 text-accent" /> : <XCircle className="h-3 w-3 text-destructive" />}
                      Last: {task.lastStatus}
                    </span>
                  )}
                  {task.completedCount > 0 && (
                    <span>{task.completedCount} run{task.completedCount !== 1 ? 's' : ''}</span>
                  )}
                </div>

                {/* Expandable last output */}
                {task.lastOutput && (
                  <button
                    onClick={() => setExpanded(expanded === task.uid ? null : task.uid)}
                    className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                  >
                    {expanded === task.uid ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                    {expanded === task.uid ? 'Hide' : 'Show'} last output
                  </button>
                )}
              </div>
              {expanded === task.uid && task.lastOutput && (
                <pre className="px-4 pb-4 text-xs whitespace-pre-wrap font-mono text-muted-foreground bg-muted/30 border-t pt-3 max-h-60 overflow-y-auto">
                  {task.lastOutput}
                </pre>
              )}
            </Card>
          ))}
        </div>
      )}

      {/* Create dialog */}
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>New Scheduled Task</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            <div className="space-y-2">
              <Label>Name</Label>
              <Input placeholder="Weekly lease check" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
            </div>
            <div className="space-y-2">
              <Label>What should the agent do?</Label>
              <Textarea
                placeholder="Review all leases expiring within 60 days..."
                value={form.prompt}
                onChange={e => setForm(f => ({ ...f, prompt: e.target.value }))}
                className="min-h-[100px]"
              />
            </div>
            <div className="space-y-2">
              <Label>Schedule</Label>
              <Input placeholder="weekly, daily, every 4h, or cron: 0 9 * * 1" value={form.schedule} onChange={e => setForm(f => ({ ...f, schedule: e.target.value }))} />
              <p className="text-[10px] text-muted-foreground">Examples: weekly, daily, every 4h, monthly, or cron expression like 0 9 * * 1</p>
            </div>
            <Button onClick={handleCreate} disabled={!form.name || !form.prompt || !form.schedule} className="w-full">
              Create Scheduled Task
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default ScheduledTasksPage;
