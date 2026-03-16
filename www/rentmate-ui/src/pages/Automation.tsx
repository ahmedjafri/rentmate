import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { getToken } from "@/lib/auth";
import { Loader2, ChevronRight, ChevronDown, ChevronUp, Plus, Wand2, Trash2, CheckCircle2, XCircle } from "lucide-react";

// ─── types ───────────────────────────────────────────────────────────────────

interface HistoryEntry {
  sha: string;
  message: string;
  date: string;
  parent: string | null;
}

interface RunEntry {
  ran_at: string;
  tasks_created: number;
  outcome: 'ok' | 'error';
  error: string | null;
}

interface Automation {
  key: string;
  label: string;
  description: string;
  hint: string;
  has_params: boolean;
  enabled: boolean;
  interval_hours: number;
  warn_days?: number;
  min_vacancy_days?: number;
  script?: string;
  custom?: boolean;
}

// ─── helpers ─────────────────────────────────────────────────────────────────

function authHeaders() {
  const t = getToken();
  return { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) };
}

function automationsToChecks(automations: Automation[]) {
  return Object.fromEntries(
    automations.map(a => [a.key, {
      enabled: a.enabled,
      interval_hours: a.interval_hours,
      ...(a.warn_days !== undefined ? { warn_days: a.warn_days } : {}),
      ...(a.min_vacancy_days !== undefined ? { min_vacancy_days: a.min_vacancy_days } : {}),
    }])
  );
}

async function fetchAutomations(): Promise<Automation[]> {
  const res = await fetch("/automations", { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to load automation config");
  const data = await res.json();
  return data.automations as Automation[];
}

async function saveAutomations(automations: Automation[], message?: string, versioned = true): Promise<Automation[]> {
  const res = await fetch("/automations", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ checks: automationsToChecks(automations), message, versioned }),
  });
  if (!res.ok) throw new Error("Failed to save config");
  const data = await res.json();
  return data.automations as Automation[];
}

async function fetchHistory(): Promise<HistoryEntry[]> {
  const res = await fetch("/automations/history", { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to load history");
  const data = await res.json();
  return data.history as HistoryEntry[];
}

async function fetchRuns(): Promise<Record<string, RunEntry[]>> {
  const res = await fetch("/automations/runs", { headers: authHeaders() });
  if (!res.ok) return {};
  const data = await res.json();
  return data.runs as Record<string, RunEntry[]>;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

async function revertConfig(sha: string): Promise<Automation[]> {
  const res = await fetch("/automations/revert", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ sha }),
  });
  if (!res.ok) throw new Error("Revert failed");
  const data = await res.json();
  return data.automations as Automation[];
}

async function createAutomation(label: string, description: string, interval_hours: number, script?: string): Promise<Automation[]> {
  const res = await fetch("/automations/new", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ label, description, interval_hours, ...(script ? { script } : {}) }),
  });
  if (!res.ok) throw new Error("Failed to create automation");
  const data = await res.json();
  return data.automations as Automation[];
}

async function validateScript(script: string): Promise<{ valid: boolean; errors: string[] }> {
  const res = await fetch("/automations/validate", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ script }),
  });
  if (!res.ok) throw new Error("Validation request failed");
  return res.json();
}

async function deleteAutomation(key: string): Promise<Automation[]> {
  const res = await fetch(`/automations/${key}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail ?? "Failed to delete automation");
  }
  return (await res.json()).automations as Automation[];
}

async function streamGenerateScript(
  label: string,
  description: string,
  onThinking: (text: string) => void,
): Promise<string> {
  const res = await fetch("/automations/generate-script", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ label, description }),
  });
  if (!res.ok || !res.body) throw new Error("Failed to generate script");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let finalScript = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const event = JSON.parse(line.slice(6));
        if (event.type === "thinking") onThinking(event.text);
        else if (event.type === "done") finalScript = event.script;
        else if (event.type === "error") throw new Error(event.message);
      } catch (e) { if (e instanceof SyntaxError) continue; throw e; }
    }
  }
  return finalScript;
}

// ─── component ───────────────────────────────────────────────────────────────

export default function Automation() {
  const navigate = useNavigate();
  const [automations, setAutomations] = useState<Automation[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [reverting, setReverting] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, RunEntry[]>>({});

  const [dialogOpen, setDialogOpen] = useState(false);
  const [newLabel, setNewLabel] = useState("");
  const [newScriptTab, setNewScriptTab] = useState<"nl" | "dsl">("dsl");
  const [newNlPrompt, setNewNlPrompt] = useState("");
  const [newScript, setNewScript] = useState("");
  const [thinkingLog, setThinkingLog] = useState<string[]>([]);
  const [scriptValidation, setScriptValidation] = useState<{ valid: boolean; errors: string[] } | null>(null);
  const [validating, setValidating] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  const loadHistory = () => fetchHistory().then(setHistory).catch(() => {});

  useEffect(() => {
    fetchAutomations()
      .then(setAutomations)
      .catch(() => toast.error("Could not load automation config"))
      .finally(() => setLoading(false));
    loadHistory();
    fetchRuns().then(setRuns);
  }, []);

  const persist = async (next: Automation[], message: string, versioned = true) => {
    setSaving(true);
    try {
      const updated = await saveAutomations(next, message, versioned);
      setAutomations(updated);
      if (versioned) await loadHistory();
    } catch {
      toast.error("Failed to save config");
    } finally {
      setSaving(false);
    }
  };

  const handleRevert = async (sha: string) => {
    setReverting(sha);
    try {
      const updated = await revertConfig(sha);
      setAutomations(updated);
      await loadHistory();
      toast.success("Reverted successfully");
    } catch {
      toast.error("Revert failed");
    } finally {
      setReverting(null);
    }
  };

  const toggleCheck = (key: string, enabled: boolean) => {
    const automation = automations.find(a => a.key === key);
    if (!automation) return;
    const next = automations.map(a => a.key === key ? { ...a, enabled } : a);
    setAutomations(next);
    persist(next, `${enabled ? "Enable" : "Disable"} ${automation.label} check`, false);
  };

  const handleValidateScript = async () => {
    if (!newScript.trim()) return;
    setValidating(true);
    try {
      const result = await validateScript(newScript);
      setScriptValidation(result);
    } catch {
      toast.error("Validation request failed");
    } finally {
      setValidating(false);
    }
  };

  const handleDelete = async (key: string, label: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Delete "${label}"? This cannot be undone.`)) return;
    setDeleting(key);
    try {
      const updated = await deleteAutomation(key);
      setAutomations(updated);
      toast.success(`Deleted "${label}"`);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setDeleting(null);
    }
  };

  const handleGenerateScript = async () => {
    if (!newNlPrompt.trim()) return;
    setGenerating(true);
    setScriptValidation(null);
    setThinkingLog([]);
    setNewScript("");
    try {
      const script = await streamGenerateScript(
        newLabel.trim(),
        newNlPrompt.trim(),
        (text) => setThinkingLog(prev => [...prev, text]),
      );
      setNewScript(script);
    } catch {
      toast.error("Failed to generate script");
    } finally {
      setGenerating(false);
    }
  };

  const handleCreate = async () => {
    if (!newLabel.trim()) return;
    setCreating(true);
    try {
      const updated = await createAutomation(newLabel.trim(), newNlPrompt.trim(), 1, newScript || undefined);
      setAutomations(updated);
      setDialogOpen(false);
      setNewLabel("");
      setNewDescription("");
      setNewScript("");
      setNewNlPrompt("");
      setNewScriptTab("dsl");
      setScriptValidation(null);
      setThinkingLog([]);
      toast.success("Automation created");
    } catch {
      toast.error("Failed to create automation");
    } finally {
      setCreating(false);
    }
  };

  const enabledCount = automations.filter(a => a.enabled).length;

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">
      {/* header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Automations</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {enabledCount} of {automations.length} enabled
            {saving && <span className="ml-2 text-xs">(saving…)</span>}
          </p>
        </div>
        <Button onClick={() => setDialogOpen(true)} className="gap-2">
          <Plus className="h-4 w-4" />
          New Automation
        </Button>
      </div>

      {/* automation list */}
      <Card>
        {automations.map((automation, index) => (
          <div
            key={automation.key}
            className={`flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-muted/40 transition-colors ${index > 0 ? "border-t" : ""}`}
            onClick={() => navigate(`/automation/${automation.key}`)}
          >
            {/* status dot */}
            <div className={`w-2 h-2 rounded-full shrink-0 ${automation.enabled ? "bg-green-500" : "bg-muted-foreground/25"}`} />

            {/* name + description + params */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium text-sm">{automation.label}</span>
                {automation.enabled && automation.warn_days !== undefined && (
                  <Badge variant="secondary" className="text-xs font-normal">
                    Warn {automation.warn_days}d before
                  </Badge>
                )}
                {automation.enabled && automation.min_vacancy_days !== undefined && automation.min_vacancy_days > 0 && (
                  <Badge variant="secondary" className="text-xs font-normal">
                    After {automation.min_vacancy_days}d vacant
                  </Badge>
                )}
              </div>
              <p className="text-xs text-muted-foreground truncate">{automation.description}</p>
              {runs[automation.key]?.length > 0 && (
                <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                  {runs[automation.key].slice(0, 5).map((run, i) => (
                    <div
                      key={i}
                      title={run.outcome === 'error' ? `Error: ${run.error}` : `${run.tasks_created} task${run.tasks_created !== 1 ? 's' : ''} created`}
                      className="flex items-center gap-1"
                    >
                      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                        run.outcome === 'error' ? 'bg-destructive' :
                        run.tasks_created > 0 ? 'bg-accent' : 'bg-muted-foreground/30'
                      }`} />
                      <span className="text-[10px] text-muted-foreground">
                        {run.outcome === 'error' ? 'error' : run.tasks_created > 0 ? `${run.tasks_created} task${run.tasks_created !== 1 ? 's' : ''}` : 'clean'}
                        {' · '}{relativeTime(run.ran_at)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* enable/disable */}
            <div onClick={e => e.stopPropagation()}>
              <Switch
                checked={automation.enabled}
                onCheckedChange={v => toggleCheck(automation.key, v)}
              />
            </div>

            {automation.custom && (
              <div onClick={e => e.stopPropagation()}>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-muted-foreground hover:text-destructive"
                  disabled={deleting === automation.key}
                  onClick={e => handleDelete(automation.key, automation.label, e)}
                >
                  {deleting === automation.key
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    : <Trash2 className="h-3.5 w-3.5" />}
                </Button>
              </div>
            )}

            <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" />
          </div>
        ))}
      </Card>

      {/* history */}
      <Card>
        <CardHeader className="cursor-pointer py-3" onClick={() => setHistoryOpen(o => !o)}>
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">Config History</CardTitle>
            <div className="flex items-center gap-2">
              <Badge variant="secondary">{history.length}</Badge>
              {historyOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </div>
          </div>
          <CardDescription>Every change is saved as a revision. Click Revert to restore.</CardDescription>
        </CardHeader>
        {historyOpen && (
          <CardContent className="pt-0 divide-y">
            {history.length === 0 && (
              <p className="text-sm text-muted-foreground py-3">No history yet — changes will appear here.</p>
            )}
            {history.map((entry, i) => (
              <div key={entry.sha} className="flex items-center justify-between py-2.5 gap-4">
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate">{entry.message}</p>
                  <p className="text-xs text-muted-foreground font-mono">
                    {entry.sha} · {new Date(entry.date).toLocaleString()}
                    {i === 0 && <span className="ml-2 text-primary">← current</span>}
                  </p>
                </div>
                {i !== 0 && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="shrink-0"
                    disabled={reverting === entry.sha}
                    onClick={() => handleRevert(entry.sha)}
                  >
                    {reverting === entry.sha ? <Loader2 className="h-3 w-3 animate-spin" /> : "Revert"}
                  </Button>
                )}
              </div>
            ))}
          </CardContent>
        )}
      </Card>

      {/* new automation dialog */}
      <Dialog open={dialogOpen} onOpenChange={open => { setDialogOpen(open); if (!open) { setNewLabel(""); setNewScript(""); setNewNlPrompt(""); setNewScriptTab("dsl"); setScriptValidation(null); setThinkingLog([]); } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>New Automation</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label htmlFor="new-label">Name</Label>
              <Input
                id="new-label"
                placeholder="e.g. Lease Review Reminder"
                value={newLabel}
                onChange={e => setNewLabel(e.target.value)}
              />
            </div>

            {/* DSL editor — primary */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label>Property-Flow DSL</Label>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="gap-1.5 h-7 text-xs"
                  disabled={generating}
                  onClick={() => setNewScriptTab(newScriptTab === "nl" ? "dsl" : "nl")}
                >
                  <Wand2 className="h-3 w-3" />
                  Generate with AI
                </Button>
              </div>

              {/* AI generation panel */}
              {newScriptTab === "nl" && (
                <div className="rounded-md border bg-muted/30 p-3 space-y-2">
                  <Textarea
                    autoFocus
                    placeholder="Describe what this automation should do, e.g. 'Alert me when a tenant has no phone number or email on file'"
                    rows={3}
                    value={newNlPrompt}
                    onChange={e => setNewNlPrompt(e.target.value)}
                    className="resize-none text-sm bg-background"
                  />
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      className="gap-2"
                      disabled={!newNlPrompt.trim() || generating}
                      onClick={handleGenerateScript}
                    >
                      {generating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />}
                      {generating ? "Generating…" : "Generate"}
                    </Button>
                    <Button type="button" variant="ghost" size="sm" onClick={() => setNewScriptTab("dsl")}>
                      Cancel
                    </Button>
                  </div>

                  {/* thinking display */}
                  {thinkingLog.length > 0 && (
                    <div className="rounded border border-dashed border-muted-foreground/30 bg-background p-2 max-h-28 overflow-y-auto space-y-0.5">
                      <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide mb-1">Thinking</p>
                      {thinkingLog.map((t, i) => (
                        <p key={i} className="text-xs text-muted-foreground leading-snug">{t}</p>
                      ))}
                    </div>
                  )}
                </div>
              )}

              <Textarea
                placeholder="Paste or write Property-Flow YAML here…"
                rows={8}
                value={newScript}
                onChange={e => { setNewScript(e.target.value); setScriptValidation(null); }}
                className="resize-none text-xs font-mono"
              />
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="gap-2"
                  disabled={!newScript.trim() || validating}
                  onClick={handleValidateScript}
                >
                  {validating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                  Validate
                </Button>
              </div>
              {scriptValidation && (
                scriptValidation.valid
                  ? <p className="text-xs text-green-600 flex items-center gap-1"><CheckCircle2 className="h-3.5 w-3.5" /> Script is valid</p>
                  : <div className="space-y-1">
                      {scriptValidation.errors.map((e, i) => (
                        <p key={i} className="text-xs text-destructive flex items-start gap-1"><XCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />{e}</p>
                      ))}
                    </div>
              )}
              {!newScript.trim()
                ? <p className="text-xs text-muted-foreground">Add a script above, or create without one and add it later.</p>
                : !scriptValidation
                  ? <p className="text-xs text-muted-foreground">Validate the script before creating.</p>
                  : null
              }
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button
              onClick={handleCreate}
              disabled={!newLabel.trim() || creating || (!!newScript.trim() && scriptValidation?.valid !== true)}
            >
              {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
