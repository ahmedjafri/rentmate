import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { authFetch } from '@/lib/auth';
import { useApp } from '@/context/AppContext';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  ArrowLeft, FileText, Building2, User, Bot, CheckCircle2,
  ChevronDown, ChevronUp, Loader2, AlertCircle, MessageSquare,
  FileType, Cpu, Hash, Layers, Link2, X, Home,
} from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { formatDistanceToNow } from 'date-fns';

// ── types ──────────────────────────────────────────────────────────────────────

interface ExtractionMeta {
  text_extractor?: string;
  llm_model?: string;
  page_count?: number;
  raw_text_chars?: number;
  form_fields_found?: number;
  form_fields_filled?: number;
  input_chars_sent_to_llm?: number;
  leases_found?: number;
}

interface DocumentDetail {
  id: string;
  filename: string;
  document_type: string;
  status: string;
  progress?: string;
  extracted_data?: Record<string, unknown>;
  extraction_meta?: ExtractionMeta;
  raw_text?: string;
  error_message?: string;
  created_at?: string;
  processed_at?: string;
}

interface PropertyCandidate {
  id: string;
  name?: string;
  address: string;
  property_type: string;
  score: number;
}

interface SuggestionGroup {
  group_id: string;
  category: 'location' | 'tenant' | 'lease';
  lease_index: number;
  title: string;
  description: string;
  suggestion_ids: string[];
  fields: Record<string, string | number | null>;
  state: string;
  candidates?: PropertyCandidate[];
}

interface ChatMessage { role: 'user' | 'assistant'; content: string; }

interface DocumentTagRecord {
  id: string;
  tag_type: 'property' | 'unit' | 'tenant';
  property_id?: string | null;
  unit_id?: string | null;
  tenant_id?: string | null;
  label?: string;
}

// ── constants ──────────────────────────────────────────────────────────────────

const categoryConfig = {
  location: { icon: Building2, label: 'Property & Unit',  pill: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' },
  tenant:   { icon: User,      label: 'Tenant',           pill: 'bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400' },
  lease:    { icon: FileText,  label: 'Lease',            pill: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' },
} as const;

const fieldLabels: Record<string, string> = {
  property_address:  'Property Address',
  property_type:     'Property Type',
  unit_label:        'Unit',
  tenant_first_name: 'First Name',
  tenant_last_name:  'Last Name',
  tenant_email:      'Email',
  tenant_phone:      'Phone',
  lease_start_date:  'Start Date',
  lease_end_date:    'End Date',
  monthly_rent:      'Monthly Rent ($)',
};

const groupFields: Record<string, string[]> = {
  location: ['property_address', 'property_type', 'unit_label'],
  tenant:   ['tenant_first_name', 'tenant_last_name', 'tenant_email', 'tenant_phone'],
  lease:    ['lease_start_date', 'lease_end_date', 'monthly_rent'],
};

const actionLabels: Record<string, string> = {
  property:        'Property',
  unit:            'Unit',
  tenant:          'Tenant',
  lease:           'Lease',
  tenant_updated:  'Tenant updated',
  lease_updated:   'Lease updated',
};

// ── DocumentPage ────────────────────────────────────────────────────────────────

const DocumentPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { setEntityContext, properties, tenants } = useApp();

  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [groups, setGroups] = useState<SuggestionGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [rawTextOpen, setRawTextOpen] = useState(false);

  // Suggestion interaction state — keyed by group_id
  const [fieldEdits, setFieldEdits] = useState<Record<string, Record<string, string>>>({});
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  // property override: lease_index → existing property_id
  const [propertyOverrides, setPropertyOverrides] = useState<Record<number, string>>({});

  const [declining, setDeclining] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [confirmResult, setConfirmResult] = useState<{ created: string[] } | null>(null);
  const [chatOpen, setChatOpen] = useState<string | null>(null);
  const [chatHistory, setChatHistory] = useState<Record<string, ChatMessage[]>>({});
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);

  // Document links (tags)
  const [tags, setTags] = useState<DocumentTagRecord[]>([]);
  const [tagSearch, setTagSearch] = useState<Record<'property' | 'unit' | 'tenant', string>>({ property: '', unit: '', tenant: '' });
  const [tagSaving, setTagSaving] = useState<string | null>(null); // entity id being saved/removed

  useEffect(() => {
    if (!id) return;
    Promise.all([
      authFetch(`/api/document/${id}`).then(r => r.ok ? r.json() : null),
      authFetch(`/api/document/${id}/suggestions`).then(r => r.ok ? r.json() : { groups: [] }),
      authFetch(`/api/document/${id}/tags`).then(r => r.ok ? r.json() : []),
    ]).then(([docData, sugData, tagsData]) => {
      setDoc(docData);
      setGroups(sugData.groups || []);
      setTags(tagsData || []);
    }).catch(() => {}).finally(() => setLoading(false));
  }, [id]);

  const handleAddTag = async (tagType: 'property' | 'unit' | 'tenant', entityId: string, label: string) => {
    if (!id) return;
    setTagSaving(entityId);
    try {
      const body: Record<string, string> = { tag_type: tagType };
      if (tagType === 'property') body.property_id = entityId;
      if (tagType === 'unit') body.unit_id = entityId;
      if (tagType === 'tenant') body.tenant_id = entityId;
      const res = await authFetch(`/api/document/${id}/tags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        const data = await res.json();
        if (!data.existed) {
          setTags(prev => [...prev, { id: data.id, tag_type: tagType, ...body, label }]);
        }
        setTagSearch(prev => ({ ...prev, [tagType]: '' }));
      }
    } catch { toast.error('Failed to add link'); }
    finally { setTagSaving(null); }
  };

  const handleRemoveTag = async (tagId: string) => {
    setTagSaving(tagId);
    try {
      const res = await authFetch(`/api/document-tag/${tagId}`, { method: 'DELETE' });
      if (res.ok) setTags(prev => prev.filter(t => t.id !== tagId));
    } catch { toast.error('Failed to remove link'); }
    finally { setTagSaving(null); }
  };

  const getFields = useCallback((group: SuggestionGroup): Record<string, string | number | null> => {
    const base = { ...group.fields };
    const edits = fieldEdits[group.group_id] || {};
    const keys = groupFields[group.category] || Object.keys(base);
    const merged: Record<string, string | number | null> = {};
    for (const k of keys) {
      merged[k] = k in edits ? (edits[k] === '' ? null : edits[k]) : (base[k] ?? null);
    }
    return merged;
  }, [fieldEdits]);

  const setField = (groupId: string, key: string, value: string) =>
    setFieldEdits(prev => ({ ...prev, [groupId]: { ...(prev[groupId] || {}), [key]: value } }));

  const toggleExcluded = (groupId: string) =>
    setExcluded(prev => {
      const next = new Set(prev);
      next.has(groupId) ? next.delete(groupId) : next.add(groupId);
      return next;
    });

  const handleDecline = async (group: SuggestionGroup) => {
    if (!id) return;
    setDeclining(group.group_id);
    try {
      const res = await authFetch(`/api/document/${id}/suggestion-group/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group_id: group.group_id }),
      });
      if (res.ok) {
        setGroups(prev => prev.filter(g => g.group_id !== group.group_id));
      } else {
        toast.error('Failed to decline suggestion');
      }
    } catch {
      toast.error('Failed to decline suggestion');
    } finally {
      setDeclining(null);
    }
  };

  const handleConfirmAll = async () => {
    if (!id) return;
    setConfirming(true);
    try {
      const payload = {
        groups: groups.map(g => ({
          group_id: g.group_id,
          category: g.category,
          lease_index: g.lease_index,
          suggestion_ids: g.suggestion_ids,
          fields: getFields(g),
        })),
        excluded: Array.from(excluded),
        property_id_overrides: Object.fromEntries(
          Object.entries(propertyOverrides).map(([k, v]) => [String(k), v])
        ),
      };
      const res = await authFetch(`/api/document/${id}/confirm-all`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        setConfirmResult(data);
        setConfirmed(true);
        // Write NL context for each location group that was confirmed
        for (const g of groups) {
          if (g.category !== 'location' || excluded.has(g.group_id)) continue;
          const override = propertyOverrides[g.lease_index];
          // We don't have the property_id in the response directly, but we can set context
          // keyed by address for now — the EntityContextCard will pick it up by property_id
          // after the property loads. Skip for now: context is set via EntityContextCard.
          void override;
        }
        const label = (data.created || []).map((c: string) => actionLabels[c] || c).filter(Boolean).join(', ');
        toast.success(label ? `Created: ${label}` : 'Confirmed — no new records needed');
      } else {
        const err = await res.text().catch(() => '');
        toast.error(`Failed to confirm: ${err || res.status}`);
      }
    } catch {
      toast.error('Failed to confirm');
    } finally {
      setConfirming(false);
    }
  };

  const handleChat = async (group: SuggestionGroup) => {
    if (!chatInput.trim() || !id) return;
    const userMsg: ChatMessage = { role: 'user', content: chatInput.trim() };
    const history = chatHistory[group.group_id] || [];
    setChatHistory(prev => ({ ...prev, [group.group_id]: [...history, userMsg] }));
    setChatInput('');
    setChatLoading(true);
    try {
      const res = await authFetch(`/api/document/${id}/suggestion-group/chat`, {
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
        setChatHistory(prev => ({ ...prev, [group.group_id]: [...(prev[group.group_id] || []), { role: 'assistant', content: data.reply }] }));
        if (data.fields) {
          const keys = groupFields[group.category] || [];
          const newEdits: Record<string, string> = {};
          for (const k of keys) {
            const v = data.fields[k];
            newEdits[k] = v === null || v === undefined ? '' : String(v);
          }
          setFieldEdits(prev => ({ ...prev, [group.group_id]: newEdits }));
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

  // ── render ──────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="p-8 flex items-center gap-3 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        Loading document…
      </div>
    );
  }

  if (!doc) {
    return (
      <div className="p-8">
        <Button variant="ghost" size="sm" onClick={() => navigate('/documents')} className="mb-4 gap-2">
          <ArrowLeft className="h-4 w-4" /> Back
        </Button>
        <p className="text-muted-foreground">Document not found.</p>
      </div>
    );
  }

  const meta = doc.extraction_meta || {};
  const statusColor = doc.status === 'done' ? 'bg-accent/15 text-accent' : doc.status === 'error' ? 'bg-destructive/15 text-destructive' : 'bg-primary/15 text-primary';

  // Group labels by lease_index for multi-property display
  const leaseIndices = [...new Set(groups.map(g => g.lease_index))].sort();
  const multiLease = leaseIndices.length > 1;

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      {/* Header */}
      <div>
        <Button variant="ghost" size="sm" onClick={() => navigate('/documents')} className="mb-3 gap-1.5 -ml-2 text-muted-foreground">
          <ArrowLeft className="h-4 w-4" /> Documents
        </Button>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-muted">
            <FileText className="h-5 w-5 text-muted-foreground" />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-bold truncate">{doc.filename}</h1>
            <div className="flex items-center gap-2 flex-wrap mt-1">
              <Badge variant="secondary" className={cn('text-[10px] rounded-md', statusColor)}>
                {doc.status}
              </Badge>
              <Badge variant="secondary" className="text-[10px] rounded-md">
                {doc.document_type}
              </Badge>
              {doc.created_at && (
                <span className="text-xs text-muted-foreground">
                  Uploaded {formatDistanceToNow(new Date(doc.created_at), { addSuffix: true })}
                </span>
              )}
              {doc.processed_at && (
                <span className="text-xs text-muted-foreground">
                  · Processed {formatDistanceToNow(new Date(doc.processed_at), { addSuffix: true })}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Error */}
      {doc.status === 'error' && doc.error_message && (
        <div className="flex items-start gap-2 rounded-lg bg-destructive/10 border border-destructive/20 p-3 text-destructive text-sm">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          {doc.error_message}
        </div>
      )}

      {/* Extraction Pipeline */}
      {(doc.status === 'done' || meta.text_extractor) && (
        <Card className="rounded-xl">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Extraction Pipeline</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 pt-0">
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <div className="flex h-6 w-6 items-center justify-center rounded-md bg-muted">
                  <FileType className="h-3.5 w-3.5 text-muted-foreground" />
                </div>
                <span className="text-sm font-medium">Text Extraction</span>
                <Badge variant="secondary" className="text-[10px] rounded-md font-mono">
                  {meta.text_extractor || 'pypdf'}
                </Badge>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pl-8">
                {meta.page_count !== undefined && (
                  <Stat icon={Layers} label="Pages" value={String(meta.page_count)} />
                )}
                {meta.raw_text_chars !== undefined && (
                  <Stat icon={Hash} label="Chars extracted" value={meta.raw_text_chars.toLocaleString()} />
                )}
                {meta.form_fields_found !== undefined && (
                  <Stat icon={FileType} label="Form fields" value={`${meta.form_fields_filled ?? 0} / ${meta.form_fields_found} filled`} />
                )}
              </div>
            </div>

            <div className="border-t" />

            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <div className="flex h-6 w-6 items-center justify-center rounded-md bg-muted">
                  <Cpu className="h-3.5 w-3.5 text-muted-foreground" />
                </div>
                <span className="text-sm font-medium">LLM Extraction</span>
                {meta.llm_model && (
                  <Badge variant="secondary" className="text-[10px] rounded-md font-mono">
                    {meta.llm_model}
                  </Badge>
                )}
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pl-8">
                {meta.input_chars_sent_to_llm !== undefined && (
                  <Stat icon={Hash} label="Input to LLM" value={`${meta.input_chars_sent_to_llm.toLocaleString()} chars`} />
                )}
                {meta.leases_found !== undefined && (
                  <Stat icon={Bot} label="Properties found" value={String(meta.leases_found)} />
                )}
              </div>
            </div>

            {doc.raw_text && (
              <>
                <div className="border-t" />
                <button
                  className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                  onClick={() => setRawTextOpen(o => !o)}
                >
                  {rawTextOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                  {rawTextOpen ? 'Hide' : 'Show'} raw extracted text
                </button>
                {rawTextOpen && (
                  <pre className="rounded-lg bg-muted/40 p-3 text-[11px] leading-relaxed overflow-x-auto whitespace-pre-wrap max-h-72 overflow-y-auto font-mono">
                    {doc.raw_text}
                  </pre>
                )}
              </>
            )}
          </CardContent>
        </Card>
      )}

      {/* Document Links */}
      {(() => {
        // Build flat list of all units across properties for the unit picker
        const allUnits = properties.flatMap(p =>
          (p.unitList || []).map(u => ({ id: u.id, label: u.label, propertyName: p.name || p.address, propertyId: p.id }))
        );

        const linkedPropertyIds = new Set(tags.filter(t => t.tag_type === 'property').map(t => t.property_id!));
        const linkedUnitIds = new Set(tags.filter(t => t.tag_type === 'unit').map(t => t.unit_id!));
        const linkedTenantIds = new Set(tags.filter(t => t.tag_type === 'tenant').map(t => t.tenant_id!));

        const sections: { key: 'property' | 'unit' | 'tenant'; label: string; icon: React.ElementType }[] = [
          { key: 'property', label: 'Properties', icon: Building2 },
          { key: 'unit',     label: 'Units',      icon: Home },
          { key: 'tenant',   label: 'Tenants',    icon: User },
        ];

        return (
          <Card className="rounded-xl">
            <CardHeader className="pb-2">
              <div className="flex items-center gap-2">
                <Link2 className="h-4 w-4 text-muted-foreground" />
                <CardTitle className="text-base">Links</CardTitle>
                {tags.length > 0 && <Badge variant="secondary" className="text-[10px] rounded-md">{tags.length}</Badge>}
              </div>
            </CardHeader>
            <CardContent className="space-y-4 pt-0">
              {sections.map(({ key, label, icon: Icon }) => {
                const sectionTags = tags.filter(t => t.tag_type === key);
                const search = tagSearch[key].toLowerCase();

                let options: { id: string; label: string; subLabel?: string }[] = [];
                if (key === 'property') {
                  options = properties
                    .filter(p => !linkedPropertyIds.has(p.id))
                    .filter(p => !search || (p.name || p.address).toLowerCase().includes(search))
                    .map(p => ({ id: p.id, label: p.name || p.address, subLabel: p.name ? p.address : undefined }));
                } else if (key === 'unit') {
                  options = allUnits
                    .filter(u => !linkedUnitIds.has(u.id))
                    .filter(u => !search || u.label.toLowerCase().includes(search) || u.propertyName.toLowerCase().includes(search))
                    .map(u => ({ id: u.id, label: u.label, subLabel: u.propertyName }));
                } else {
                  options = tenants
                    .filter(t => !linkedTenantIds.has(t.id))
                    .filter(t => !search || t.name.toLowerCase().includes(search))
                    .map(t => ({ id: t.id, label: t.name, subLabel: t.unit || undefined }));
                }

                return (
                  <div key={key} className="space-y-2">
                    <div className="flex items-center gap-1.5">
                      <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                      <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</span>
                    </div>

                    {/* Linked chips */}
                    {sectionTags.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {sectionTags.map(tag => (
                          <span
                            key={tag.id}
                            className="flex items-center gap-1 text-[11px] rounded-lg bg-primary/10 text-primary px-2.5 py-1 font-medium"
                          >
                            {tag.label || tag.property_id || tag.unit_id || tag.tenant_id}
                            <button
                              onClick={() => handleRemoveTag(tag.id)}
                              disabled={tagSaving === tag.id}
                              className="ml-0.5 hover:text-destructive transition-colors"
                            >
                              {tagSaving === tag.id
                                ? <Loader2 className="h-3 w-3 animate-spin" />
                                : <X className="h-3 w-3" />}
                            </button>
                          </span>
                        ))}
                      </div>
                    )}

                    {/* Search + dropdown */}
                    <div className="relative">
                      <Input
                        placeholder={`Search ${label.toLowerCase()}…`}
                        value={tagSearch[key]}
                        onChange={e => setTagSearch(prev => ({ ...prev, [key]: e.target.value }))}
                        className="h-7 text-xs rounded-lg pr-2"
                      />
                      {tagSearch[key] && options.length > 0 && (
                        <div className="absolute z-10 mt-1 w-full rounded-lg border bg-popover shadow-md overflow-hidden">
                          {options.slice(0, 8).map(opt => (
                            <button
                              key={opt.id}
                              onClick={() => handleAddTag(key, opt.id, opt.label)}
                              disabled={tagSaving === opt.id}
                              className="w-full text-left px-3 py-2 text-xs hover:bg-muted flex items-center gap-2"
                            >
                              {tagSaving === opt.id
                                ? <Loader2 className="h-3 w-3 animate-spin shrink-0" />
                                : <Icon className="h-3 w-3 shrink-0 text-muted-foreground" />}
                              <span className="truncate">{opt.label}</span>
                              {opt.subLabel && <span className="text-muted-foreground truncate ml-auto pl-2">{opt.subLabel}</span>}
                            </button>
                          ))}
                        </div>
                      )}
                      {tagSearch[key] && options.length === 0 && (
                        <div className="absolute z-10 mt-1 w-full rounded-lg border bg-popover shadow-md px-3 py-2 text-xs text-muted-foreground">
                          No {label.toLowerCase()} found
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>
        );
      })()}

      {/* Suggestion Groups */}
      {groups.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold">Suggested Actions</h2>
            <p className="text-xs text-muted-foreground">Uncheck any entity to exclude it</p>
          </div>

          {confirmed && confirmResult ? (
            <Card className="rounded-xl border-accent/30 bg-accent/5">
              <CardContent className="p-4 space-y-2">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-accent" />
                  <p className="text-sm font-semibold text-accent">Confirmed</p>
                </div>
                {confirmResult.created.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {confirmResult.created.map((c, i) => (
                      <Badge key={i} variant="secondary" className="text-[11px] gap-1 bg-accent/15 text-accent">
                        <CheckCircle2 className="h-2.5 w-2.5" />
                        {actionLabels[c] || c}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">All records already existed — nothing new was created.</p>
                )}
              </CardContent>
            </Card>
          ) : (
            <>
              {leaseIndices.map(leaseIdx => {
                const leaseGroups = groups.filter(g => g.lease_index === leaseIdx);
                const locationGroup = leaseGroups.find(g => g.category === 'location');
                const locationAddress = locationGroup ? (getFields(locationGroup).property_address as string || '') : '';

                return (
                  <div key={leaseIdx} className="space-y-2">
                    {multiLease && (
                      <div className="flex items-center gap-2 pt-1">
                        <div className="h-px flex-1 bg-border" />
                        <span className="text-xs text-muted-foreground font-medium px-2">
                          Property {leaseIdx + 1}{locationAddress ? ` — ${locationAddress}` : ''}
                        </span>
                        <div className="h-px flex-1 bg-border" />
                      </div>
                    )}

                    {leaseGroups.map(group => {
                      const config = categoryConfig[group.category];
                      const Icon = config.icon;
                      const isIncluded = !excluded.has(group.group_id);
                      const fields = getFields(group);
                      const isChatOpen = chatOpen === group.group_id;
                      const history = chatHistory[group.group_id] || [];
                      const overriddenPropertyId = group.category === 'location' ? propertyOverrides[group.lease_index] : null;
                      const candidates = group.candidates || [];

                      return (
                        <Card
                          key={group.group_id}
                          className={cn('rounded-xl transition-opacity', !isIncluded && 'opacity-40')}
                        >
                          <CardContent className="p-4 space-y-3">
                            {/* Header with checkbox */}
                            <div className="flex items-start gap-3">
                              <input
                                type="checkbox"
                                checked={isIncluded}
                                onChange={() => toggleExcluded(group.group_id)}
                                className="mt-1 h-4 w-4 rounded accent-primary cursor-pointer shrink-0"
                              />
                              <div className="flex items-center gap-2 min-w-0 flex-1">
                                <div className={cn('flex h-7 w-7 shrink-0 items-center justify-center rounded-lg', config.pill)}>
                                  <Icon className="h-4 w-4" />
                                </div>
                                <div className="min-w-0">
                                  <p className="text-sm font-semibold truncate">{group.title}</p>
                                  <div className="flex items-center gap-1.5 flex-wrap">
                                    <span className="text-xs text-muted-foreground">{config.label}</span>
                                    {group.suggestion_ids.map(sid => (
                                      <Badge key={sid} variant="secondary" className={cn('text-[10px] rounded-md px-1.5 py-0', config.pill)}>
                                        {sid.replace(/_\d+$/, '')}
                                      </Badge>
                                    ))}
                                  </div>
                                </div>
                              </div>
                              <button
                                onClick={() => handleDecline(group)}
                                disabled={declining === group.group_id}
                                title="Decline suggestion"
                                className="ml-auto shrink-0 p-1 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors disabled:opacity-40"
                              >
                                {declining === group.group_id
                                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                  : <X className="h-3.5 w-3.5" />
                                }
                              </button>
                            </div>

                            {/* Property match candidates (location groups only) */}
                            {group.category === 'location' && isIncluded && candidates.length > 0 && (
                              <div className="pl-7 space-y-1.5">
                                <p className="text-[11px] text-muted-foreground font-medium flex items-center gap-1">
                                  <Link2 className="h-3 w-3" />
                                  Possible match{candidates.length > 1 ? 'es' : ''} in your portfolio — link instead of creating:
                                </p>
                                <div className="flex flex-wrap gap-1.5">
                                  {candidates.map(c => (
                                    <button
                                      key={c.id}
                                      onClick={() => setPropertyOverrides(prev =>
                                        prev[group.lease_index] === c.id
                                          ? Object.fromEntries(Object.entries(prev).filter(([k]) => Number(k) !== group.lease_index))
                                          : { ...prev, [group.lease_index]: c.id }
                                      )}
                                      className={cn(
                                        'text-[11px] rounded-lg px-2.5 py-1 border transition-colors',
                                        overriddenPropertyId === c.id
                                          ? 'border-primary bg-primary/10 text-primary font-medium'
                                          : 'border-border bg-muted/30 text-muted-foreground hover:border-primary/50 hover:text-foreground'
                                      )}
                                    >
                                      {c.name ? `${c.name} — ` : ''}{c.address}
                                      <span className="ml-1 opacity-60">{Math.round(c.score * 100)}%</span>
                                    </button>
                                  ))}
                                </div>
                                {overriddenPropertyId && (
                                  <p className="text-[11px] text-primary">
                                    Will link to existing property instead of creating a new one.
                                  </p>
                                )}
                              </div>
                            )}

                            {/* Editable fields (hidden when using property override) */}
                            {!(group.category === 'location' && overriddenPropertyId) && (
                              <div className="grid grid-cols-2 gap-1.5 pl-7">
                                {(groupFields[group.category] || []).map(key => {
                                  const val = fields[key];
                                  const strVal = val === null || val === undefined ? '' : String(val);
                                  const currentVal = fieldEdits[group.group_id]?.[key] ?? strVal;

                                  if (key === 'property_type') {
                                    return (
                                      <div key={key} className="bg-muted/40 rounded-md px-2.5 py-1.5">
                                        <span className="text-[10px] text-muted-foreground uppercase tracking-wide block">
                                          {fieldLabels[key]}
                                        </span>
                                        <select
                                          value={currentVal || 'multi_family'}
                                          onChange={e => setField(group.group_id, key, e.target.value)}
                                          disabled={!isIncluded}
                                          className="w-full text-xs font-medium bg-transparent border-0 p-0 focus:outline-none cursor-pointer"
                                        >
                                          <option value="multi_family">Multi-Family</option>
                                          <option value="single_family">Single Family</option>
                                        </select>
                                      </div>
                                    );
                                  }

                                  return (
                                    <div key={key} className="bg-muted/40 rounded-md px-2.5 py-1.5">
                                      <span className="text-[10px] text-muted-foreground uppercase tracking-wide block">
                                        {fieldLabels[key] || key}
                                      </span>
                                      <Input
                                        value={currentVal}
                                        onChange={e => setField(group.group_id, key, e.target.value)}
                                        disabled={!isIncluded}
                                        placeholder="—"
                                        className="h-6 text-xs border-0 bg-transparent p-0 focus-visible:ring-0 font-medium placeholder:text-muted-foreground/40"
                                      />
                                    </div>
                                  );
                                })}
                              </div>
                            )}

                            {/* Refine with AI */}
                            {isIncluded && !overriddenPropertyId && (
                              <div className="pl-7">
                                <button
                                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                                  onClick={() => setChatOpen(isChatOpen ? null : group.group_id)}
                                >
                                  <MessageSquare className="h-3.5 w-3.5" />
                                  Refine with AI
                                  {isChatOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                                </button>
                                {isChatOpen && (
                                  <div className="mt-2 rounded-lg border bg-background overflow-hidden">
                                    {history.length > 0 && (
                                      <div className="p-2 space-y-2 max-h-40 overflow-y-auto">
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
                                        className="h-8 text-sm rounded-md flex-1"
                                        onKeyDown={e => e.key === 'Enter' && !chatLoading && handleChat(group)}
                                        disabled={chatLoading}
                                      />
                                      <Button size="sm" className="h-8 px-3 text-xs rounded-md shrink-0"
                                        disabled={chatLoading || !chatInput.trim()}
                                        onClick={() => handleChat(group)}
                                      >
                                        {chatLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Send'}
                                      </Button>
                                    </div>
                                  </div>
                                )}
                              </div>
                            )}
                          </CardContent>
                        </Card>
                      );
                    })}
                  </div>
                );
              })}

              {/* Confirm button */}
              <Button
                className="w-full rounded-xl gap-2"
                disabled={confirming || excluded.size === groups.length}
                onClick={handleConfirmAll}
              >
                {confirming
                  ? <><Loader2 className="h-4 w-4 animate-spin" /> Confirming…</>
                  : <><CheckCircle2 className="h-4 w-4" /> Confirm {excluded.size > 0 ? `(${groups.length - excluded.size} of ${groups.length})` : 'all'}</>
                }
              </Button>
            </>
          )}
        </div>
      )}

      {/* No suggestions yet */}
      {doc.status === 'done' && !loading && groups.length === 0 && (
        <Card className="rounded-xl p-6 text-center">
          <p className="text-sm text-muted-foreground">No actionable suggestions found for this document.</p>
        </Card>
      )}
    </div>
  );
};

// Small stat tile
function Stat({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: string }) {
  return (
    <div className="bg-muted/40 rounded-md px-2.5 py-2">
      <div className="flex items-center gap-1 mb-0.5">
        <Icon className="h-3 w-3 text-muted-foreground" />
        <span className="text-[10px] text-muted-foreground uppercase tracking-wide">{label}</span>
      </div>
      <span className="text-xs font-semibold">{value}</span>
    </div>
  );
}

export default DocumentPage;
