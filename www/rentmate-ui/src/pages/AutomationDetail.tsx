import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import { getToken } from "@/lib/auth";
import { Loader2, Play, Zap, ChevronLeft, ChevronDown, ChevronUp, Wand2, Save, PlusCircle, CheckCircle2, Trash2, XCircle, Star, Wrench } from "lucide-react";
import { listVendors } from "@/graphql/client";

// ─── types ───────────────────────────────────────────────────────────────────

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
  simulation_run?: boolean;
  require_vendor_type?: string;
  vendor_ids?: string[];
  preferred_vendor_id?: string | null;
}

interface Vendor {
  id: string;
  name: string;
  company?: string;
  vendorType?: string;
  phone?: string;
  email?: string;
}

interface SimulatedTask {
  subject: string;
  category: string;
  urgency: string;
  source: string;
  property_id: string | null;
  unit_id: string | null;
  description: string;
  assigned_vendor_id?: string | null;
  assigned_vendor_name?: string | null;
  autonomy?: string;
}

const AUTONOMY_LABELS: Record<string, string> = {
  manual: "Notify only",
  suggest: "Suggest & wait",
  autonomous: "Fully autonomous",
};

const AUTONOMY_COLORS: Record<string, string> = {
  manual: "bg-slate-100 text-slate-700",
  suggest: "bg-amber-100 text-amber-800",
  autonomous: "bg-green-100 text-green-800",
};

// ─── colors ──────────────────────────────────────────────────────────────────

const URGENCY_COLORS: Record<string, string> = {
  low: "bg-slate-100 text-slate-700",
  medium: "bg-yellow-100 text-yellow-800",
  high: "bg-orange-100 text-orange-800",
  critical: "bg-red-100 text-red-800",
};

const CATEGORY_COLORS: Record<string, string> = {
  leasing: "bg-blue-100 text-blue-800",
  rent: "bg-green-100 text-green-800",
  maintenance: "bg-purple-100 text-purple-800",
  compliance: "bg-pink-100 text-pink-800",
};

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
      ...(a.vendor_ids?.length ? { vendor_ids: a.vendor_ids } : {}),
      ...(a.preferred_vendor_id ? { preferred_vendor_id: a.preferred_vendor_id } : {}),
    }])
  );
}

async function fetchAutomations(): Promise<Automation[]> {
  const res = await fetch("/automations", { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to load config");
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

async function runSimulate(check: string): Promise<SimulatedTask[]> {
  const res = await fetch("/automations/simulate", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ check }),
  });
  if (!res.ok) throw new Error("Simulation failed");
  const data = await res.json();
  return data.tasks as SimulatedTask[];
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

async function validateScript(script: string): Promise<{ valid: boolean; errors: string[] }> {
  const res = await fetch("/automations/validate", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ script }),
  });
  if (!res.ok) throw new Error("Validation request failed");
  return res.json();
}

async function deleteAutomation(key: string): Promise<void> {
  const res = await fetch(`/automations/${key}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail ?? "Failed to delete automation");
  }
}

async function saveScript(key: string, script: string): Promise<Automation[]> {
  const res = await fetch("/automations/update-script", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ key, script }),
  });
  if (!res.ok) throw new Error("Failed to save script");
  return (await res.json()).automations as Automation[];
}

// ─── component ───────────────────────────────────────────────────────────────

export default function AutomationDetail() {
  const { key } = useParams<{ key: string }>();
  const navigate = useNavigate();

  const [automations, setAutomations] = useState<Automation[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [simulating, setSimulating] = useState(false);
  const [simResults, setSimResults] = useState<SimulatedTask[] | null>(null);
  const [simOpen, setSimOpen] = useState(true);
  const [createdTasks, setCreatedTasks] = useState<Set<string>>(new Set());
  const [creatingTask, setCreatingTask] = useState<string | null>(null);

  const [scriptTab, setScriptTab] = useState<"nl" | "dsl">("dsl");
  const [scriptDraft, setScriptDraft] = useState("");
  const [nlPrompt, setNlPrompt] = useState("");
  const [thinkingLog, setThinkingLog] = useState<string[]>([]);
  const [generatingScript, setGeneratingScript] = useState(false);
  const [savingScript, setSavingScript] = useState(false);
  const [scriptValidation, setScriptValidation] = useState<{ valid: boolean; errors: string[] } | null>(null);
  const [validatingScript, setValidatingScript] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [vendorsLoading, setVendorsLoading] = useState(false);

useEffect(() => {
    fetchAutomations()
      .then(list => {
        setAutomations(list);
        const found = list.find(a => a.key === key);
        if (found?.script) setScriptDraft(found.script);
        if (found?.description) setNlPrompt(found.description);
      })
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false));
  }, [key]);

  useEffect(() => {
    setVendorsLoading(true);
    listVendors()
      .then(data => {
        setVendors((data.vendors ?? []).map(v => ({
          id: v.uid,
          name: v.name,
          company: v.company,
          vendorType: v.vendorType,
          phone: v.phone,
          email: v.email,
        })));
      })
      .catch(() => {})
      .finally(() => setVendorsLoading(false));
  }, []);

  const automation = automations.find(a => a.key === key);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="p-6 max-w-2xl mx-auto space-y-2">
        <p className="text-muted-foreground">Failed to load automation config.</p>
        <p className="text-xs text-muted-foreground">If this is a fresh install, restart the server so the database table is created, then try again.</p>
        <Button variant="link" className="pl-0" onClick={() => navigate("/automation")}>
          ← Back to Automations
        </Button>
      </div>
    );
  }

  if (!automation) {
    return (
      <div className="p-6 max-w-2xl mx-auto">
        <p className="text-muted-foreground">Automation not found.</p>
        <Button variant="link" className="pl-0 mt-2" onClick={() => navigate("/automation")}>
          ← Back to Automations
        </Button>
      </div>
    );
  }

  const patch = async (updates: Partial<Automation>, message: string, versioned = true) => {
    setSaving(true);
    const next = automations.map(a => a.key === key ? { ...a, ...updates } : a);
    setAutomations(next);
    try {
      const updated = await saveAutomations(next, message, versioned);
      setAutomations(updated);
    } catch {
      toast.error("Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const matchingVendors = vendors.filter(
    v => !automation?.require_vendor_type || v.vendorType === automation.require_vendor_type
  );

  const toggleVendor = (vendorId: string, selected: boolean) => {
    const currentIds = automation?.vendor_ids ?? [];
    let nextIds: string[];
    if (selected) {
      nextIds = [...currentIds, vendorId];
    } else {
      nextIds = currentIds.filter(id => id !== vendorId);
    }
    // If removing the preferred vendor, clear it
    const nextPreferred = nextIds.includes(automation?.preferred_vendor_id ?? '')
      ? automation?.preferred_vendor_id
      : (nextIds[0] ?? null);
    patch(
      { vendor_ids: nextIds, preferred_vendor_id: nextPreferred },
      `Update vendors for ${automation?.label}`,
      false,
    );
  };

  const setPreferredVendor = (vendorId: string) => {
    patch(
      { preferred_vendor_id: vendorId },
      `Set preferred vendor for ${automation?.label}`,
      false,
    );
  };

  const handleSimulate = async () => {
    setSimulating(true);
    setSimResults(null);
    setSimOpen(true);
    setCreatedTasks(new Set());
    try {
      const tasks = await runSimulate(key!);
      setSimResults(tasks);
      if (tasks.length === 0) toast.success("No new tasks would be created.");
      // Re-fetch so simulation_run flag updates (enables toggle for custom automations)
      fetchAutomations().then(setAutomations).catch(() => {});
    } catch {
      toast.error("Simulation failed");
    } finally {
      setSimulating(false);
    }
  };

  const taskKey = (t: SimulatedTask) =>
    `${t.subject}::${t.property_id ?? ''}::${t.unit_id ?? ''}`;

  const handleSimulatedAction = async (t: SimulatedTask, endpoint: string, successMsg: string) => {
    const key = taskKey(t);
    setCreatingTask(key);
    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          subject: t.subject,
          category: t.category,
          urgency: t.urgency,
          body: t.description,
          property_id: t.property_id,
          unit_id: t.unit_id,
          automation_key: automation?.key,
        }),
      });
      if (res.status === 409) { toast.info("Already exists"); return; }
      if (!res.ok) throw new Error();
      setCreatedTasks(prev => new Set(prev).add(key));
      toast.success(successMsg);
    } catch {
      toast.error("Action failed");
    } finally {
      setCreatingTask(null);
    }
  };

  const handleGenerateScript = async () => {
    if (!nlPrompt.trim() || !automation) return;
    setGeneratingScript(true);
    setScriptValidation(null);
    setThinkingLog([]);
    setScriptDraft("");
    try {
      const script = await streamGenerateScript(
        automation.label,
        nlPrompt.trim(),
        (text) => setThinkingLog(prev => [...prev, text]),
      );
      setScriptDraft(script);
      setScriptTab("dsl");
    } catch {
      toast.error("Failed to generate script");
    } finally {
      setGeneratingScript(false);
    }
  };

  const handleValidateScript = async () => {
    if (!scriptDraft.trim()) return;
    setValidatingScript(true);
    try {
      const result = await validateScript(scriptDraft);
      setScriptValidation(result);
    } catch {
      toast.error("Validation request failed");
    } finally {
      setValidatingScript(false);
    }
  };

  const handleSaveScript = async () => {
    if (!automation || !scriptDraft.trim()) return;
    setSavingScript(true);
    try {
      const updated = await saveScript(automation.key, scriptDraft.trim());
      setAutomations(updated);
      toast.success("Script saved");
    } catch {
      toast.error("Failed to save script");
    } finally {
      setSavingScript(false);
    }
  };

  const handleDelete = async () => {
    if (!automation) return;
    if (!confirm(`Delete "${automation.label}"? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      await deleteAutomation(automation.key);
      toast.success(`Deleted "${automation.label}"`);
      navigate("/automation");
    } catch (err) {
      toast.error((err as Error).message);
      setDeleting(false);
    }
  };

  return (
    <div className="p-6 max-w-2xl mx-auto space-y-6">
      {/* back + header */}
      <div>
        <button
          onClick={() => navigate("/automation")}
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-3 transition-colors"
        >
          <ChevronLeft className="h-4 w-4" />
          Automations
        </button>
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className={`w-2.5 h-2.5 rounded-full ${automation.enabled ? "bg-green-500" : "bg-muted-foreground/25"}`} />
            <div>
              <h1 className="text-2xl font-bold">{automation.label}</h1>
              <p className="text-sm text-muted-foreground mt-0.5">{automation.description}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {automation.custom && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-muted-foreground hover:text-destructive"
                disabled={deleting}
                onClick={handleDelete}
              >
                {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
              </Button>
            )}
            <div className="flex flex-col items-end gap-1">
              <Switch
                checked={automation.enabled}
                disabled={
                  (automation.custom && !automation.simulation_run) ||
                  (!!automation.require_vendor_type && !(automation.vendor_ids?.length))
                }
                onCheckedChange={v => patch({ enabled: v }, `${v ? "Enable" : "Disable"} ${automation.label} check`, false)}
              />
              {automation.custom && !automation.simulation_run && (
                <p className="text-xs text-muted-foreground text-right">Run a simulation first</p>
              )}
              {!!automation.require_vendor_type && !(automation.vendor_ids?.length) && (
                <p className="text-xs text-muted-foreground text-right">Select a vendor first</p>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* editable params — only for checks that have configurable parameters */}
      {automation.has_params && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Parameters</CardTitle>
            <CardDescription>{automation.hint}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {automation.warn_days !== undefined && (
              <div className="flex items-center gap-3">
                <span className="text-sm">Warn</span>
                <Input
                  type="number"
                  min={1}
                  max={365}
                  value={automation.warn_days}
                  onChange={e => {
                    const d = Math.max(1, parseInt(e.target.value) || 1);
                    patch({ warn_days: d }, `Set ${automation.label} warning to ${d} day${d !== 1 ? "s" : ""} before end`);
                  }}
                  className="w-20"
                />
                <span className="text-sm text-muted-foreground">
                  day{automation.warn_days !== 1 ? "s" : ""} before lease end
                  {saving && <span className="ml-2 text-xs">(saving…)</span>}
                </span>
              </div>
            )}
            {automation.min_vacancy_days !== undefined && (
              <div className="flex items-center gap-3">
                <span className="text-sm">Only flag units vacant for at least</span>
                <Input
                  type="number"
                  min={0}
                  max={365}
                  value={automation.min_vacancy_days}
                  onChange={e => {
                    const d = Math.max(0, parseInt(e.target.value) || 0);
                    patch({ min_vacancy_days: d }, `Set ${automation.label} vacancy threshold to ${d} day${d !== 1 ? "s" : ""}`);
                  }}
                  className="w-20"
                />
                <span className="text-sm text-muted-foreground">
                  day{automation.min_vacancy_days !== 1 ? "s" : ""}
                  {saving && <span className="ml-2 text-xs">(saving…)</span>}
                </span>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* vendor picker — shown when the automation requires a vendor type */}
      {automation.require_vendor_type && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2">
              <Wrench className="h-4 w-4 text-muted-foreground" />
              <div>
                <CardTitle className="text-base">Vendor Assignment</CardTitle>
                <CardDescription>
                  Select one or more <strong>{automation.require_vendor_type}</strong> vendors.
                  The preferred vendor will be pre-assigned when tasks are created.
                  At least one is required to enable this automation.
                </CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {vendorsLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading vendors…
              </div>
            ) : matchingVendors.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No {automation.require_vendor_type} vendors found.{" "}
                <a href="/vendors" className="underline text-primary">Add one in the Vendors page</a>.
              </p>
            ) : (
              <div className="divide-y rounded-md border">
                {matchingVendors.map(vendor => {
                  const isSelected = (automation.vendor_ids ?? []).includes(vendor.id);
                  const isPreferred = automation.preferred_vendor_id === vendor.id;
                  return (
                    <div
                      key={vendor.id}
                      className={`flex items-center gap-3 px-3 py-2.5 ${isSelected ? "bg-muted/30" : ""}`}
                    >
                      {/* checkbox */}
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={e => toggleVendor(vendor.id, e.target.checked)}
                        className="h-4 w-4 rounded border-input accent-primary cursor-pointer"
                      />
                      {/* info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="text-sm font-medium">{vendor.name}</span>
                          {vendor.company && (
                            <span className="text-xs text-muted-foreground">· {vendor.company}</span>
                          )}
                        </div>
                        {(vendor.phone || vendor.email) && (
                          <p className="text-xs text-muted-foreground">
                            {[vendor.phone, vendor.email].filter(Boolean).join(" · ")}
                          </p>
                        )}
                      </div>
                      {/* preferred star — only shown when this vendor is selected */}
                      {isSelected && (
                        <button
                          onClick={() => setPreferredVendor(vendor.id)}
                          title={isPreferred ? "Preferred vendor" : "Set as preferred"}
                          className="shrink-0 p-1 rounded hover:bg-muted transition-colors"
                        >
                          <Star
                            className={`h-4 w-4 ${isPreferred ? "fill-yellow-400 text-yellow-400" : "text-muted-foreground"}`}
                          />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
            {(automation.vendor_ids?.length ?? 0) > 0 && (
              <p className="text-xs text-muted-foreground">
                {automation.vendor_ids!.length} vendor{automation.vendor_ids!.length !== 1 ? "s" : ""} selected
                {automation.preferred_vendor_id && (() => {
                  const pv = vendors.find(v => v.id === automation.preferred_vendor_id);
                  return pv ? ` · ${pv.name} preferred` : "";
                })()}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* simulate */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Simulate</CardTitle>
          <CardDescription>Preview what tasks this check would create right now, without saving them.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button onClick={handleSimulate} disabled={simulating} className="gap-2">
            {simulating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            Run simulation
          </Button>

          {simResults !== null && (
            <div className="rounded-md border bg-muted/30 p-3 space-y-1.5">
              <div
                className="flex items-center justify-between cursor-pointer"
                onClick={() => setSimOpen(o => !o)}
              >
                <span className="text-sm font-medium flex items-center gap-2">
                  <Zap className="h-4 w-4 text-yellow-500" />
                  {simResults.length === 0
                    ? "No new tasks would be created"
                    : `${simResults.length} task${simResults.length !== 1 ? "s" : ""} would be created`}
                </span>
                {simOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              </div>
              {simOpen && simResults.length > 0 && (
                <div className="space-y-2 pt-1">
                  {simResults.map((t, i) => {
                    const key = taskKey(t);
                    const done = createdTasks.has(key);
                    const loading = creatingTask === key;
                    const autonomy = t.autonomy ?? "suggest";
                    const isAutonomous = autonomy === "autonomous";
                    return (
                      <div key={i} className={`rounded border bg-background p-3 space-y-1.5 ${done ? "opacity-60" : ""}`}>
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex items-center gap-2 flex-wrap min-w-0">
                            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${CATEGORY_COLORS[t.category] ?? "bg-slate-100 text-slate-700"}`}>
                              {t.category}
                            </span>
                            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${URGENCY_COLORS[t.urgency] ?? "bg-slate-100 text-slate-700"}`}>
                              {t.urgency}
                            </span>
                            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${AUTONOMY_COLORS[autonomy] ?? "bg-slate-100 text-slate-700"}`}>
                              {AUTONOMY_LABELS[autonomy] ?? autonomy}
                            </span>
                            <span className="text-sm font-medium">{t.subject}</span>
                          </div>
                          {done ? (
                            <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0 mt-0.5" />
                          ) : isAutonomous ? (
                            <Button
                              size="sm"
                              variant="default"
                              className="gap-1 h-7 text-xs shrink-0"
                              disabled={loading}
                              onClick={() => handleSimulatedAction(t, "/automations/simulate/create-task", "Task created")}
                            >
                              {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <PlusCircle className="h-3 w-3" />}
                              Create task
                            </Button>
                          ) : (
                            <Button
                              size="sm"
                              variant="outline"
                              className="gap-1 h-7 text-xs shrink-0"
                              disabled={loading}
                              onClick={() => handleSimulatedAction(t, "/automations/simulate/create-suggestion", "Suggestion added")}
                            >
                              {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Star className="h-3 w-3" />}
                              Suggest
                            </Button>
                          )}
                        </div>
                        {t.description && (
                          <p className="text-xs text-muted-foreground whitespace-pre-line leading-relaxed">
                            {t.description}
                          </p>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* script */}
      {automation.custom ? (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-base">Property-Flow DSL</CardTitle>
                <CardDescription>Define the automation logic in YAML.</CardDescription>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="gap-1.5 shrink-0"
                disabled={generatingScript}
                onClick={() => { setScriptTab(scriptTab === "nl" ? "dsl" : "nl"); setThinkingLog([]); }}
              >
                <Wand2 className="h-3.5 w-3.5" />
                Generate with AI
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {/* AI generation panel */}
            {scriptTab === "nl" && (
              <div className="rounded-md border bg-muted/30 p-3 space-y-2">
                <Textarea
                  autoFocus
                  placeholder="Describe what this automation should do, e.g. 'Alert me when a unit has been vacant for more than 30 days'"
                  rows={3}
                  value={nlPrompt}
                  onChange={e => setNlPrompt(e.target.value)}
                  className="resize-none text-sm bg-background"
                />
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    className="gap-2"
                    disabled={!nlPrompt.trim() || generatingScript}
                    onClick={handleGenerateScript}
                  >
                    {generatingScript ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />}
                    {generatingScript ? "Generating…" : "Generate"}
                  </Button>
                  <Button type="button" variant="ghost" size="sm" onClick={() => setScriptTab("dsl")}>
                    Cancel
                  </Button>
                </div>

                {/* thinking display */}
                {thinkingLog.length > 0 && (
                  <div className="rounded border border-dashed border-muted-foreground/30 bg-background p-2 max-h-32 overflow-y-auto space-y-0.5">
                    <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide mb-1">Thinking</p>
                    {thinkingLog.map((t, i) => (
                      <p key={i} className="text-xs text-muted-foreground leading-snug">{t}</p>
                    ))}
                  </div>
                )}
              </div>
            )}

            <Textarea
              rows={12}
              value={scriptDraft}
              onChange={e => { setScriptDraft(e.target.value); setScriptValidation(null); }}
              className="resize-none text-xs font-mono"
              placeholder="Paste or write Property-Flow YAML here…"
            />
            <div className="flex items-center gap-2 flex-wrap">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="gap-2"
                disabled={!scriptDraft.trim() || validatingScript}
                onClick={handleValidateScript}
              >
                {validatingScript ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                Validate
              </Button>
              <Button
                type="button"
                size="sm"
                className="gap-2"
                disabled={!scriptDraft.trim() || savingScript || (scriptValidation !== null && !scriptValidation.valid)}
                onClick={handleSaveScript}
              >
                {savingScript ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                Save Script
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
          </CardContent>
        </Card>
      ) : automation.script ? (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Script</CardTitle>
            <CardDescription>Property-Flow DSL definition for this automation.</CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="text-xs font-mono bg-muted rounded-md p-4 overflow-x-auto whitespace-pre leading-relaxed">
              {automation.script}
            </pre>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
