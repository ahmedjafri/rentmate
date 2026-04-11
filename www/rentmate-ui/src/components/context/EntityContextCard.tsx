import { useState, useMemo } from 'react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { ScrollArea } from '@/components/ui/scroll-area';
import { FileText, AlertTriangle, CheckCircle2, Bot, ChevronRight, Loader2 } from 'lucide-react';
import { useApp } from '@/context/AppContext';
import { getEntityNote, saveEntityNote, updateEntityContext } from '@/graphql/client';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import { Globe, Lock } from 'lucide-react';

export interface ContextTopic {
  key: string;
  label: string;
  description: string;
}

export interface AutoContext {
  label: string;
  value: string;
}

interface EntityContextCardProps {
  entityId: string;
  entityName: string;
  entityType?: 'property' | 'unit' | 'tenant' | 'vendor' | 'document';
  /** Agent-managed context from the DB */
  agentContext?: string;
  /** Callback when agent context is saved to DB */
  onAgentContextSaved?: (newContext: string) => void;
  /** Topics the user should cover in their context notes */
  expectedTopics?: ContextTopic[];
  /** Auto-generated context lines derived from system data */
  autoContext?: AutoContext[];
}

function countWords(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

function checkTopicCoverage(text: string, topics: ContextTopic[]): { covered: ContextTopic[]; missing: ContextTopic[] } {
  const lower = text.toLowerCase();
  const covered: ContextTopic[] = [];
  const missing: ContextTopic[] = [];
  for (const topic of topics) {
    const keywords = [topic.key, ...topic.label.toLowerCase().split(/\s+/)];
    const found = keywords.some(kw => lower.includes(kw.toLowerCase()));
    if (found) covered.push(topic);
    else missing.push(topic);
  }
  return { covered, missing };
}

type ContextHealth = 'red' | 'yellow' | 'green';

function getContextHealth(wordCount: number, missingCount: number, totalTopics: number): ContextHealth {
  if (totalTopics === 0) {
    if (wordCount === 0) return 'red';
    if (wordCount < 20) return 'yellow';
    return 'green';
  }
  const coverage = (totalTopics - missingCount) / totalTopics;
  if (wordCount < 10 || coverage < 0.25) return 'red';
  if (wordCount < 30 || coverage < 0.6) return 'yellow';
  return 'green';
}

const healthConfig: Record<ContextHealth, { label: string; cardClass: string; dotClass: string }> = {
  red: { label: 'Needs context', cardClass: 'border-destructive/40', dotClass: 'bg-destructive' },
  yellow: { label: 'Partial', cardClass: 'border-warning/40', dotClass: 'bg-warning' },
  green: { label: 'Good', cardClass: 'border-accent/40', dotClass: 'bg-accent' },
};

export const propertyTopics: ContextTopic[] = [
  { key: 'acquired', label: 'Acquisition', description: 'When and how the property was acquired' },
  { key: 'problem', label: 'Recurring problems', description: 'Known recurring issues (plumbing, pests, noise, etc.)' },
  { key: 'financ', label: 'Finances (mortgage, taxes, insurance)', description: 'Mortgage payments, insurance, property taxes, and other costs' },
  { key: 'vendor', label: 'Preferred vendors', description: 'Preferred contractors, vendors, or service providers' },
  { key: 'rule', label: 'Rules, policies & HOA', description: 'Special rules, HOA policies, dues, or local regulations' },
];

export function EntityContextCard({ entityId, entityName, entityType, agentContext, onAgentContextSaved, expectedTopics = [], autoContext = [] }: EntityContextCardProps) {
  const { getEntityContext, setEntityContext } = useApp();
  const [open, setOpen] = useState(false);
  const context = getEntityContext(entityId);
  const [draft, setDraft] = useState('');
  const [sharedDraft, setSharedDraft] = useState('');
  const [privateDraft, setPrivateDraft] = useState('');
  const [privateNotes, setPrivateNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const autoContextText = useMemo(() => autoContext.map(a => `${a.label} ${a.value}`).join(' '), [autoContext]);

  const allText = [context, agentContext || '', privateNotes, autoContextText].join(' ');
  const wordCount = countWords(allText);

  const { missing } = useMemo(() => checkTopicCoverage(allText, expectedTopics), [allText, expectedTopics]);
  const health = getContextHealth(wordCount, missing.length, expectedTopics.length);
  const hc = healthConfig[health];

  const handleOpen = async () => {
    setDraft(context);
    setSharedDraft(agentContext || '');
    // Fetch private notes from DB (not supported for documents)
    if (entityType && entityType !== 'document') {
      try {
        const result = await getEntityNote(entityType, entityId);
        const notes = result.entityNote || '';
        setPrivateDraft(notes);
        setPrivateNotes(notes);
      } catch {
        setPrivateDraft('');
      }
    }
    setOpen(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      // Save human notes to localStorage
      setEntityContext(entityId, draft);

      // Save shared context if changed
      if (entityType && sharedDraft !== (agentContext || '')) {
        await updateEntityContext(entityType, entityId, sharedDraft.trim());
        onAgentContextSaved?.(sharedDraft.trim());
      }

      // Save private notes if changed (not supported for documents)
      if (entityType && entityType !== 'document' && privateDraft !== privateNotes) {
        await saveEntityNote(entityType, entityId, privateDraft.trim());
        setPrivateNotes(privateDraft.trim());
      }

      toast.success('Context saved');
      setOpen(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const draftAllText = [draft, sharedDraft, privateDraft, autoContextText].join(' ');
  const draftWordCount = countWords(draftAllText);
  const draftCoverage = useMemo(() => checkTopicCoverage(draftAllText, expectedTopics), [draftAllText, expectedTopics]);
  const draftHealth = getContextHealth(draftWordCount, draftCoverage.missing.length, expectedTopics.length);
  const draftHc = healthConfig[draftHealth];

  return (
    <>
      <Card
        className={cn('p-4 rounded-xl cursor-pointer hover:shadow-md transition-shadow border', hc.cardClass)}
        onClick={handleOpen}
      >
        <div className="flex items-center gap-2 mb-1">
          <FileText className="h-4 w-4 text-muted-foreground" />
          <span className="text-xs text-muted-foreground">Context</span>
          <div className={cn('h-2 w-2 rounded-full ml-auto', hc.dotClass)} title={hc.label} />
        </div>
        <p className="text-xl font-bold">{wordCount}</p>
        <p className="text-[11px] text-muted-foreground">
          {wordCount === 1 ? 'word' : 'words'}
        </p>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-3xl h-[90vh] flex flex-col gap-0 p-0">
          <DialogHeader className="p-5 pb-3 border-b">
            <div className="flex items-center justify-between">
              <DialogTitle className="text-base">Context — {entityName}</DialogTitle>
              <div className={cn('flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full', {
                'bg-destructive/10 text-destructive': draftHealth === 'red',
                'bg-warning/10 text-warning-foreground': draftHealth === 'yellow',
                'bg-accent/10 text-accent': draftHealth === 'green',
              })}>
                <div className={cn('h-1.5 w-1.5 rounded-full', draftHc.dotClass)} />
                {draftHc.label}
              </div>
            </div>
          </DialogHeader>

          <ScrollArea className="flex-1 min-h-0">
            <div className="p-5 space-y-4">
              {/* Auto-generated context */}
              {autoContext.length > 0 && (
                <Collapsible>
                  <CollapsibleTrigger className="flex items-center gap-1.5 w-full group">
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground transition-transform group-data-[state=open]:rotate-90" />
                    <Bot className="h-3.5 w-3.5 text-primary" />
                    <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Auto-generated</span>
                    <span className="text-[10px] text-muted-foreground ml-1">({countWords(autoContextText)} words)</span>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <div className="rounded-lg bg-muted/50 border p-3 space-y-1.5 mt-2">
                      {autoContext.map((item, i) => (
                        <div key={i} className="flex items-baseline gap-2 text-sm">
                          <span className="text-muted-foreground text-xs font-medium shrink-0">{item.label}:</span>
                          <span>{item.value}</span>
                        </div>
                      ))}
                    </div>
                  </CollapsibleContent>
                </Collapsible>
              )}

              {/* Shared context (visible to all accounts) */}
              {entityType && (
                <div>
                  <div className="flex items-center gap-1.5 mb-2">
                    <Globe className="h-3.5 w-3.5 text-blue-500" />
                    <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Shared Context</span>
                    <span className="text-[10px] text-muted-foreground ml-1">(visible to all accounts)</span>
                    {sharedDraft && (
                      <span className="text-[10px] text-muted-foreground">· {countWords(sharedDraft)} words</span>
                    )}
                  </div>
                  <Textarea
                    value={sharedDraft}
                    onChange={(e) => setSharedDraft(e.target.value)}
                    placeholder="Objective facts: lease terms, property features, extraction data. Set by document processing or agent."
                    className="min-h-[80px] resize-none text-sm font-mono"
                  />
                </div>
              )}

              {/* Private notes (per-account) — not shown for documents */}
              {entityType && entityType !== 'document' && (
                <div>
                  <div className="flex items-center gap-1.5 mb-2">
                    <Lock className="h-3.5 w-3.5 text-orange-500" />
                    <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Private Notes</span>
                    <span className="text-[10px] text-muted-foreground ml-1">(only your account)</span>
                    {privateDraft && (
                      <span className="text-[10px] text-muted-foreground">· {countWords(privateDraft)} words</span>
                    )}
                  </div>
                  <Textarea
                    value={privateDraft}
                    onChange={(e) => setPrivateDraft(e.target.value)}
                    placeholder="Account-specific observations, assessments, strategies, preferences."
                    className="min-h-[80px] resize-none text-sm font-mono"
                  />
                </div>
              )}

              {/* Missing topics warnings */}
              {draftCoverage.missing.length > 0 && (
                <div className="flex items-start gap-2 rounded-lg bg-warning/5 border border-warning/20 px-3 py-2.5">
                  <AlertTriangle className="h-3.5 w-3.5 text-warning-foreground mt-1 shrink-0" />
                  <div>
                    <span className="text-xs font-medium">Consider adding:</span>
                    <ul className="mt-1 space-y-0.5">
                      {draftCoverage.missing.map(t => (
                        <li key={t.key} className="text-xs text-muted-foreground flex items-center gap-1.5">
                          <span className="h-1 w-1 rounded-full bg-warning-foreground/50 shrink-0" />
                          {t.label}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}

              {/* Covered topics */}
              {draftCoverage.covered.length > 0 && (
                <div>
                  <div className="flex items-center gap-1.5 mb-2">
                    <CheckCircle2 className="h-3.5 w-3.5 text-accent" />
                    <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Covered</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {draftCoverage.covered.map(topic => (
                      <span key={topic.key} className="text-[11px] rounded-md bg-accent/10 text-accent px-2 py-0.5 font-medium">
                        {topic.label}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* User notes */}
              <div>
                <div className="flex items-center gap-1.5 mb-2">
                  <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Your notes</span>
                </div>
                <Textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder={`Add context about ${entityName}...\n\nInclude details like acquisition history, recurring problems, concerns, future plans, budgets, preferred vendors, and any special rules or policies.`}
                  className="min-h-[180px] resize-none text-sm"
                />
              </div>
            </div>
          </ScrollArea>

          <div className="flex items-center justify-between p-4 pt-3 border-t">
            <span className="text-xs text-muted-foreground">
              {draftWordCount} {draftWordCount === 1 ? 'word' : 'words'}
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
              <Button size="sm" onClick={handleSave} disabled={saving}>
                {saving ? <Loader2 className="h-3 w-3 animate-spin mr-1.5" /> : null}
                Save
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
