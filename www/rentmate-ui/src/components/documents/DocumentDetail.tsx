import { useState, useEffect } from 'react';
import { authFetch } from '@/lib/auth';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  Building2, User, FileText, CheckCircle2, XCircle,
  Loader2, AlertCircle, MessageSquare, ChevronDown, ChevronUp,
} from 'lucide-react';
import { ManagedDocument } from '@/data/mockData';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';

interface SuggestionGroup {
  group_id: string;
  category: 'location' | 'tenant' | 'lease';
  title: string;
  description: string;
  suggestion_ids: string[];
  fields: Record<string, string | number | null>;
  state: string;
}

interface ChatMessage { role: 'user' | 'assistant'; content: string; }

const categoryConfig: Record<string, { icon: React.ElementType; label: string; pill: string }> = {
  location: { icon: Building2, label: 'Property & Unit',  pill: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' },
  tenant:   { icon: User,      label: 'Tenant',           pill: 'bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400' },
  lease:    { icon: FileText,  label: 'Lease',            pill: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' },
};

// Full contextual labels (used in flat contexts — include entity prefix)
const fieldLabels: Record<string, string> = {
  property_address: 'Property Address',
  unit_label:       'Unit',
  tenant_first_name: 'First Name',
  tenant_last_name:  'Last Name',
  tenant_email:      'Email',
  tenant_phone:      'Phone',
  lease_start_date:  'Start Date',
  lease_end_date:    'End Date',
  monthly_rent:      'Monthly Rent ($)',
};

const actionLabels: Record<string, string> = {
  create_property: 'New property',
  create_unit:     'New unit',
  create_tenant:   'New tenant',
  create_lease:    'New lease',
  update_tenant:   'Update tenant',
  update_lease:    'Update lease',
};

// Which fields belong to each group (determines display order too)
const groupFields: Record<string, string[]> = {
  location: ['property_address', 'unit_label'],
  tenant:   ['tenant_first_name', 'tenant_last_name', 'tenant_email', 'tenant_phone'],
  lease:    ['lease_start_date', 'lease_end_date', 'monthly_rent'],
};

const MANUAL_FIELDS: { key: string; label: string; placeholder: string; section: string }[] = [
  { key: 'property_address', label: 'Property Address', placeholder: '123 Main St, Seattle, WA', section: 'location' },
  { key: 'unit_label',       label: 'Unit',             placeholder: '2B',                         section: 'location' },
  { key: 'tenant_first_name', label: 'First Name',      placeholder: 'Jane',                       section: 'tenant' },
  { key: 'tenant_last_name',  label: 'Last Name',       placeholder: 'Smith',                      section: 'tenant' },
  { key: 'tenant_email',      label: 'Email',           placeholder: 'jane@example.com',           section: 'tenant' },
  { key: 'tenant_phone',      label: 'Phone',           placeholder: '+1 206 555 0100',            section: 'tenant' },
  { key: 'lease_start_date',  label: 'Start Date',      placeholder: 'YYYY-MM-DD',                 section: 'lease' },
  { key: 'lease_end_date',    label: 'End Date',        placeholder: 'YYYY-MM-DD',                 section: 'lease' },
  { key: 'monthly_rent',      label: 'Monthly Rent ($)', placeholder: '2000',                      section: 'lease' },
];

const sectionConfig = {
  location: { icon: Building2, label: 'Property & Unit', pill: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' },
  tenant:   { icon: User,      label: 'Tenant',          pill: 'bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400' },
  lease:    { icon: FileText,  label: 'Lease',           pill: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' },
};

function ManualEntry({ docId }: { docId: string }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [applying, setApplying] = useState(false);
  const [created, setCreated] = useState<string[] | null>(null);

  const set = (key: string, value: string) => setValues(prev => ({ ...prev, [key]: value }));

  const handleApply = async () => {
    setApplying(true);
    try {
      const payload: Record<string, string | number | null> = {};
      MANUAL_FIELDS.forEach(({ key }) => {
        const v = values[key]?.trim();
        payload[key] = key === 'monthly_rent' && v ? parseFloat(v) : (v || null);
      });
      const res = await authFetch(`/api/document/${docId}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        setCreated(data.created || []);
        toast.success(`Applied: ${(data.created || []).map((c: string) => actionLabels[c] || c).join(', ') || 'Done'}`);
      } else {
        toast.error('Failed to apply');
      }
    } catch {
      toast.error('Failed to apply');
    } finally {
      setApplying(false);
    }
  };

  if (created !== null) {
    return (
      <div className="rounded-xl border p-3 space-y-2">
        <p className="text-sm font-medium">Applied manually</p>
        {created.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {created.map(c => (
              <Badge key={c} variant="secondary" className="text-[10px] gap-1 bg-accent/15 text-accent">
                <CheckCircle2 className="h-2.5 w-2.5" />
                {actionLabels[c] || c}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">All entities already exist — nothing new was created.</p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="rounded-lg bg-amber-50 border border-amber-200 dark:bg-amber-900/20 dark:border-amber-800 px-3 py-2">
        <p className="text-xs text-amber-800 dark:text-amber-300 font-medium">No data extracted automatically</p>
        <p className="text-[11px] text-amber-700 dark:text-amber-400 mt-0.5">
          The document may be a blank template or use a format the AI couldn't read. Enter the details below.
        </p>
      </div>

      {(['location', 'tenant', 'lease'] as const).map(section => {
        const cfg = sectionConfig[section];
        const Icon = cfg.icon;
        const fields = MANUAL_FIELDS.filter(f => f.section === section);
        return (
          <div key={section} className="rounded-xl border p-3 space-y-2">
            <div className="flex items-center gap-2">
              <div className={cn('flex h-6 w-6 items-center justify-center rounded-md', cfg.pill)}>
                <Icon className="h-3.5 w-3.5" />
              </div>
              <span className="text-sm font-medium">{cfg.label}</span>
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              {fields.map(({ key, label, placeholder }) => (
                <div key={key} className="bg-muted/40 rounded-md px-2.5 py-1.5">
                  <span className="text-[10px] text-muted-foreground uppercase tracking-wide block">{label}</span>
                  <Input
                    value={values[key] ?? ''}
                    onChange={e => set(key, e.target.value)}
                    placeholder={placeholder}
                    className="h-6 text-xs border-0 bg-transparent p-0 focus-visible:ring-0 font-medium placeholder:text-muted-foreground/50"
                  />
                </div>
              ))}
            </div>
          </div>
        );
      })}

      <Button
        size="sm" className="rounded-lg text-xs gap-1.5"
        disabled={applying || Object.values(values).every(v => !v?.trim())}
        onClick={handleApply}
      >
        {applying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
        Apply all
      </Button>
    </div>
  );
}

interface DocumentDetailProps {
  doc: ManagedDocument;
}

const DocumentDetail = ({ doc }: DocumentDetailProps) => {
  const [groups, setGroups] = useState<SuggestionGroup[]>([]);
  const [loading, setLoading] = useState(false);
  // fieldEdits stores per-category overrides keyed by field name
  const [fieldEdits, setFieldEdits] = useState<Record<string, Record<string, string>>>({});
  const [applying, setApplying] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, { created: string[] }>>({});
  const [groupStates, setGroupStates] = useState<Record<string, 'pending' | 'accepted' | 'rejected'>>({});
  const [chatOpen, setChatOpen] = useState<string | null>(null);
  const [chatHistory, setChatHistory] = useState<Record<string, ChatMessage[]>>({});
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);

  useEffect(() => {
    if (doc.status !== 'ready') return;
    setLoading(true);
    authFetch(`/api/document/${doc.id}/suggestions`)
      .then(r => r.ok ? r.json() : { groups: [] })
      .then(data => {
        setGroups(data.groups || []);
        const states: Record<string, 'pending' | 'accepted' | 'rejected'> = {};
        (data.groups || []).forEach((g: SuggestionGroup) => {
          states[g.category] = (g.state === 'accepted' || g.state === 'rejected') ? g.state : 'pending';
        });
        setGroupStates(states);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [doc.id, doc.status]);

  // Returns the effective fields for a group: base fields merged with any user edits,
  // ordered and filtered according to the group's field list.
  const getFields = (group: SuggestionGroup): Record<string, string | number | null> => {
    const base = { ...group.fields };
    const edits = fieldEdits[group.category] || {};
    const keys = groupFields[group.category] || Object.keys(base);
    const merged: Record<string, string | number | null> = {};
    for (const k of keys) {
      merged[k] = k in edits ? (edits[k] === '' ? null : edits[k]) : (base[k] ?? null);
    }
    return merged;
  };

  const setField = (category: string, key: string, value: string) =>
    setFieldEdits(prev => ({ ...prev, [category]: { ...(prev[category] || {}), [key]: value } }));

  const handleAccept = async (group: SuggestionGroup) => {
    setApplying(group.category);
    try {
      const res = await authFetch(`/api/document/${doc.id}/suggestion-group/accept`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category: group.category,
          suggestion_ids: group.suggestion_ids,
          fields: getFields(group),
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setResults(prev => ({ ...prev, [group.category]: data }));
        setGroupStates(prev => ({ ...prev, [group.category]: 'accepted' }));
        const label = (data.created || []).map((c: string) => actionLabels[c] || c).join(', ');
        toast.success(`Applied: ${label || group.title}`);
      } else {
        toast.error('Failed to apply');
      }
    } catch {
      toast.error('Failed to apply');
    } finally {
      setApplying(null);
    }
  };

  const handleReject = async (group: SuggestionGroup) => {
    await authFetch(`/api/document/${doc.id}/suggestion-group/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category: group.category }),
    });
    setGroupStates(prev => ({ ...prev, [group.category]: 'rejected' }));
  };

  const handleChat = async (group: SuggestionGroup) => {
    if (!chatInput.trim()) return;
    const userMsg: ChatMessage = { role: 'user', content: chatInput.trim() };
    const history = chatHistory[group.category] || [];
    setChatHistory(prev => ({ ...prev, [group.category]: [...history, userMsg] }));
    setChatInput('');
    setChatLoading(true);
    try {
      const res = await authFetch(`/api/document/${doc.id}/suggestion-group/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category: group.category,
          fields: getFields(group),
          description: group.description,
          message: userMsg.content,
          history: history.map(m => ({ role: m.role, content: m.content })),
        }),
      });
      if (res.ok) {
        const data = await res.json();
        const aiMsg: ChatMessage = { role: 'assistant', content: data.reply };
        setChatHistory(prev => ({ ...prev, [group.category]: [...(prev[group.category] || []), aiMsg] }));
        // Replace field edits wholesale with the AI's updated field set
        if (data.fields) {
          const keys = groupFields[group.category] || [];
          const newEdits: Record<string, string> = {};
          for (const k of keys) {
            const v = data.fields[k];
            newEdits[k] = v === null || v === undefined ? '' : String(v);
          }
          setFieldEdits(prev => ({ ...prev, [group.category]: newEdits }));
        }
      } else {
        toast.error('AI unavailable');
      }
    } catch {
      toast.error('Chat unavailable');
    } finally {
      setChatLoading(false);
    }
  };

  // ─── Analyzing state ──────────────────────────────────────────────────────────

  if (doc.status === 'analyzing') {
    return (
      <div className="px-4 pb-4 pt-3 border-t flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin shrink-0" />
        Extracting lease details…
      </div>
    );
  }

  if (doc.status === 'error') {
    return (
      <div className="px-4 pb-4 pt-3 border-t flex items-center gap-2 text-sm text-destructive">
        <AlertCircle className="h-4 w-4 shrink-0" />
        {doc.errorMessage || 'Processing failed'}
      </div>
    );
  }

  // ─── Ready state ──────────────────────────────────────────────────────────────

  return (
    <div className="px-4 pb-4 space-y-3 border-t pt-3">
      {loading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading extracted data…
        </div>
      )}

      {!loading && groups.length === 0 && <ManualEntry docId={doc.id} />}

      {/* Suggestion groups — each group shows only its own fields */}
      {groups.map(group => {
        const config = categoryConfig[group.category] || categoryConfig.location;
        const Icon = config.icon;
        const state = groupStates[group.category] || 'pending';
        const fields = getFields(group);
        const isApplying = applying === group.category;
        const isChatOpen = chatOpen === group.category;
        const history = chatHistory[group.category] || [];
        const result = results[group.category];
        // Only show fields that belong to this group category
        const visibleKeys = (groupFields[group.category] || Object.keys(fields)).filter(
          k => fields[k] !== null && fields[k] !== undefined && fields[k] !== ''
        );

        return (
          <div
            key={group.group_id}
            className={cn('rounded-xl border p-3 space-y-2.5 transition-opacity', state === 'rejected' && 'opacity-50')}
          >
            {/* Header */}
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <div className={cn('flex h-6 w-6 shrink-0 items-center justify-center rounded-md', config.pill)}>
                  <Icon className="h-3.5 w-3.5" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium leading-tight truncate">{group.title}</p>
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-[11px] text-muted-foreground">{config.label}</span>
                    {group.suggestion_ids.map(sid => (
                      <Badge key={sid} variant="secondary" className={cn('text-[10px] rounded-md px-1 py-0', config.pill)}>
                        {actionLabels[sid] || sid}
                      </Badge>
                    ))}
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-1 shrink-0">
                {state === 'pending' && (
                  <>
                    <Button
                      size="sm" variant="ghost"
                      className="h-6 text-[11px] px-2 text-muted-foreground hover:text-destructive"
                      onClick={() => handleReject(group)}
                    >
                      Skip
                    </Button>
                    <Button
                      size="sm"
                      className="h-6 text-[11px] px-2 rounded-md"
                      disabled={isApplying}
                      onClick={() => handleAccept(group)}
                    >
                      {isApplying ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Apply'}
                    </Button>
                  </>
                )}
                {state === 'accepted' && (
                  <Badge variant="secondary" className="text-[10px] gap-1 bg-accent/15 text-accent">
                    <CheckCircle2 className="h-3 w-3" />
                    Applied
                  </Badge>
                )}
                {state === 'rejected' && (
                  <Badge variant="secondary" className="text-[10px] gap-1">
                    <XCircle className="h-3 w-3" />
                    Skipped
                  </Badge>
                )}
              </div>
            </div>

            {/* Editable fields — show all keys for this group, even if empty, so user can fill them in */}
            <div className="grid grid-cols-2 gap-1.5">
              {(groupFields[group.category] || visibleKeys).map(key => {
                const val = fields[key];
                const strVal = val === null || val === undefined ? '' : String(val);
                return (
                  <div key={key} className="bg-muted/40 rounded-md px-2.5 py-1.5">
                    <span className="text-[10px] text-muted-foreground uppercase tracking-wide block">
                      {fieldLabels[key] || key}
                    </span>
                    {state === 'pending' ? (
                      <Input
                        value={fieldEdits[group.category]?.[key] ?? strVal}
                        onChange={e => setField(group.category, key, e.target.value)}
                        placeholder={strVal || '—'}
                        className="h-6 text-xs border-0 bg-transparent p-0 focus-visible:ring-0 font-medium placeholder:text-muted-foreground/40"
                      />
                    ) : (
                      <p className="text-xs font-medium">{strVal || <span className="text-muted-foreground">—</span>}</p>
                    )}
                  </div>
                );
              })}
            </div>

            {/* Applied result */}
            {state === 'accepted' && result && result.created.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {result.created.map(c => (
                  <Badge key={c} variant="secondary" className="text-[10px] gap-1 bg-accent/15 text-accent">
                    <CheckCircle2 className="h-2.5 w-2.5" />
                    {actionLabels[c] || c}
                  </Badge>
                ))}
              </div>
            )}

            {/* Refine with AI (chat) */}
            {state === 'pending' && (
              <div>
                <button
                  className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                  onClick={() => setChatOpen(isChatOpen ? null : group.category)}
                >
                  <MessageSquare className="h-3 w-3" />
                  Refine with AI
                  {isChatOpen ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                </button>

                {isChatOpen && (
                  <div className="mt-2 rounded-lg border bg-background overflow-hidden">
                    {history.length > 0 && (
                      <div className="p-2 space-y-2 max-h-32 overflow-y-auto">
                        {history.map((m, i) => (
                          <div key={i} className={cn('text-xs px-2 py-1.5 rounded-lg', m.role === 'assistant' ? 'bg-muted' : 'bg-primary/10 text-right')}>
                            {m.content}
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="flex gap-1 p-2 border-t">
                      <Input
                        value={chatInput}
                        onChange={e => setChatInput(e.target.value)}
                        placeholder={`e.g. "Change rent to $2,400"`}
                        className="h-7 text-xs rounded-md flex-1"
                        onKeyDown={e => e.key === 'Enter' && !chatLoading && handleChat(group)}
                        disabled={chatLoading}
                      />
                      <Button
                        size="sm" className="h-7 px-2 text-xs rounded-md shrink-0"
                        disabled={chatLoading || !chatInput.trim()}
                        onClick={() => handleChat(group)}
                      >
                        {chatLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Send'}
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export default DocumentDetail;
