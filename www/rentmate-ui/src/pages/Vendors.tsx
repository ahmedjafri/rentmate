import { useEffect, useMemo, useState } from 'react';
import { useApp } from '@/context/AppContext';
import { Vendor } from '@/data/mockData';
import { EntityContextCard } from '@/components/context/EntityContextCard';
import { createVendor, deleteVendor, getVendorTypes, sendSms, updateVendor as updateVendorMutation } from '@/graphql/client';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { PageLoader } from '@/components/ui/page-loader';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Wrench, Plus, Pencil, Trash2, Search, Phone, Link, Copy, CheckCircle2, Send, Loader2 } from 'lucide-react';
import { toast } from 'sonner';


const vendorTopics = [
  { key: 'specialt', label: 'Specialties', description: 'What they specialize in, certifications' },
  { key: 'rate', label: 'Rates & pricing', description: 'Hourly rate, typical job costs, payment terms' },
  { key: 'reliab', label: 'Reliability', description: 'Response time, quality of work, past experience' },
];

interface FormState {
  name: string;
  company: string;
  vendorType: string;
  phone: string;
  email: string;
  notes: string;
}

const emptyForm = (): FormState => ({
  name: '', company: '', vendorType: '', phone: '', email: '', notes: '',
});


function PortalLink({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(url);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = url;
        textarea.style.cssText = 'position:fixed;left:-9999px';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
      }
      setCopied(true);
      toast.success('Portal link copied');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('Failed to copy — select the link manually');
    }
  };

  return (
    <div className="pt-1.5 space-y-1">
      <div className="flex items-center gap-1.5">
        <Link className="h-3 w-3 text-muted-foreground" />
        <span className="text-xs text-muted-foreground">Portal link</span>
      </div>
      <div className="flex items-center gap-1.5">
        <code className="text-[11px] bg-muted px-2 py-1 rounded truncate max-w-[280px] select-all">
          {url}
        </code>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 shrink-0"
          onClick={handleCopy}
        >
          {copied ? <CheckCircle2 className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5 text-muted-foreground" />}
        </Button>
      </div>
    </div>
  );
}

const Vendors = () => {
  const { vendors, isLoading, addVendor, updateVendor, removeVendor } = useApp();
  const [vendorTypes, setVendorTypes] = useState<string[]>([]);

  useEffect(() => {
    getVendorTypes()
      .then(d => setVendorTypes(d.vendorTypes ?? []))
      .catch(() => {});
  }, []);

  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('_all');

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm());
  const [saving, setSaving] = useState(false);

  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [smsVendor, setSmsVendor] = useState<Vendor | null>(null);
  const [smsBody, setSmsBody] = useState('');
  const [smsSending, setSmsSending] = useState(false);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return vendors.filter(v => {
      if (typeFilter !== '_all' && v.vendorType !== typeFilter) return false;
      if (!q) return true;
      return (
        v.name.toLowerCase().includes(q) ||
        (v.company ?? '').toLowerCase().includes(q) ||
        (v.vendorType ?? '').toLowerCase().includes(q)
      );
    });
  }, [vendors, search, typeFilter]);

  const openAdd = () => {
    setEditingId(null);
    setForm(emptyForm());
    setDialogOpen(true);
  };

  const openEdit = (v: Vendor) => {
    setEditingId(v.id);
    setForm({
      name: v.name,
      company: v.company ?? '',
      vendorType: v.vendorType ?? '',
      phone: v.phone ?? '',
      email: v.email ?? '',
      notes: v.notes ?? '',
    });
    setDialogOpen(true);
  };

  const handleSubmit = async () => {
    if (!form.name.trim()) {
      toast.error('Name is required');
      return;
    }
    if (!form.vendorType) {
      toast.error('Vendor type is required');
      return;
    }
    if (!form.phone.trim()) {
      toast.error('Phone number is required');
      return;
    }
    setSaving(true);
    try {
      const input = {
        name: form.name.trim(),
        company: form.company.trim() || null,
        vendorType: form.vendorType || null,
        phone: form.phone.trim(),
        email: form.email.trim() || null,
        notes: form.notes.trim() || null,
      };

      if (editingId) {
        const data = await updateVendorMutation({ uid: editingId, ...input });
        updateVendor(editingId, {
          name: data.updateVendor.name,
          company: data.updateVendor.company,
          vendorType: data.updateVendor.vendorType,
          phone: data.updateVendor.phone,
          email: data.updateVendor.email,
          notes: data.updateVendor.notes,
          portalUrl: data.updateVendor.portalUrl,
        });
        toast.success('Vendor updated');
      } else {
        const data = await createVendor(input);
        addVendor({
          id: data.createVendor.uid,
          name: data.createVendor.name,
          company: data.createVendor.company,
          vendorType: data.createVendor.vendorType,
          phone: data.createVendor.phone,
          email: data.createVendor.email,
          notes: data.createVendor.notes,
          portalUrl: data.createVendor.portalUrl,
        });
        toast.success('Vendor added');
      }
      setDialogOpen(false);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    setDeleting(true);
    try {
      await deleteVendor(id);
      removeVendor(id);
      setConfirmDeleteId(null);
      toast.success('Vendor deleted');
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setDeleting(false);
    }
  };

  if (isLoading) return <PageLoader />;

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Vendors</h1>
          <p className="text-muted-foreground text-sm mt-1">
            {vendors.length} vendor{vendors.length !== 1 ? 's' : ''}
          </p>
        </div>
        <Button onClick={openAdd}>
          <Plus className="h-4 w-4 mr-2" />
          Add Vendor
        </Button>
      </div>

      {/* Filters */}
      <div className="flex gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search vendors..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
        <Select value={typeFilter} onValueChange={setTypeFilter}>
          <SelectTrigger className="w-48">
            <SelectValue placeholder="All types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="_all">All types</SelectItem>
            {vendorTypes.map(t => (
              <SelectItem key={t} value={t}>{t}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Content */}
      {vendors.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-center text-muted-foreground gap-3">
          <Wrench className="h-12 w-12 opacity-30" />
          <p className="font-medium">No vendors yet. Add your first vendor.</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground">No vendors match your search.</div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {filtered.map(v => (
            <Card key={v.id} className="p-4 relative">
              <div className="absolute top-3 right-3 flex gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => openEdit(v)}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                {confirmDeleteId === v.id ? (
                  <Button
                    variant="destructive"
                    size="sm"
                    className="h-7 text-xs px-2"
                    onClick={() => handleDelete(v.id)}
                    disabled={deleting}
                  >
                    Confirm?
                  </Button>
                ) : (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-destructive"
                    onClick={() => setConfirmDeleteId(v.id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>

              <div className="pr-16 space-y-1">
                <div className="font-semibold">{v.name}</div>
                {v.company && <div className="text-sm text-muted-foreground">{v.company}</div>}
                <div className="flex flex-wrap gap-1.5 pt-0.5">
                  {v.vendorType && (
                    <Badge variant="secondary" className="text-xs">{v.vendorType}</Badge>
                  )}
                </div>
                <div className="pt-1 space-y-0.5 text-sm text-muted-foreground">
                  {v.phone && <div>{v.phone}</div>}
                  {v.email && <div>{v.email}</div>}
                </div>
                {v.portalUrl && <PortalLink url={v.portalUrl} />}
                {v.phone && (
                  <div className="pt-1.5">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs gap-1.5"
                      onClick={() => { setSmsVendor(v); setSmsBody(''); }}
                    >
                      <Phone className="h-3 w-3" />
                      Send SMS
                    </Button>
                  </div>
                )}
              </div>
              <div className="mt-3 pt-3 border-t">
                <EntityContextCard
                  entityId={v.id}
                  entityName={v.name}
                  entityType="vendor"
                  agentContext={v.context}
                  onAgentContextSaved={(ctx) => updateVendor(v.id, { context: ctx })}
                  expectedTopics={vendorTopics}
                />
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Add / Edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md flex flex-col max-h-[90vh]">
          <DialogHeader>
            <DialogTitle>{editingId ? 'Edit Vendor' : 'Add Vendor'}</DialogTitle>
            <DialogDescription>
              {editingId
                ? 'Update the vendor record, contact details, and portal information.'
                : 'Add a vendor with their contact details and trade information.'}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2 overflow-y-auto pr-1">
            <div className="space-y-1.5">
              <Label htmlFor="v-name">Name *</Label>
              <Input
                id="v-name"
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="Jane Smith"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="v-company">Company</Label>
              <Input
                id="v-company"
                value={form.company}
                onChange={e => setForm(f => ({ ...f, company: e.target.value }))}
                placeholder="Smith Plumbing LLC"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Vendor Type *</Label>
              <Select value={form.vendorType} onValueChange={val => setForm(f => ({ ...f, vendorType: val }))}>
                <SelectTrigger>
                  <SelectValue placeholder="Select type" />
                </SelectTrigger>
                <SelectContent>
                  {vendorTypes.map(t => (
                    <SelectItem key={t} value={t}>{t}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="v-phone">Phone *</Label>
              <Input
                id="v-phone"
                value={form.phone}
                onChange={e => setForm(f => ({ ...f, phone: e.target.value }))}
                placeholder="+1 (555) 000-0000"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="v-email">Email</Label>
              <Input
                id="v-email"
                type="email"
                value={form.email}
                onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                placeholder="jane@example.com"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="v-notes">Notes</Label>
              <Textarea
                id="v-notes"
                value={form.notes}
                onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
                placeholder="Any additional notes..."
                rows={3}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button onClick={handleSubmit} disabled={saving}>
              {saving ? 'Saving...' : editingId ? 'Update' : 'Add Vendor'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* SMS Dialog */}
      <Dialog open={!!smsVendor} onOpenChange={open => { if (!open) setSmsVendor(null); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Phone className="h-4 w-4" />
              Send SMS to {smsVendor?.name}
            </DialogTitle>
            <DialogDescription>
              Send a text message to the vendor using their saved phone number.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">{smsVendor?.phone}</p>
            <Textarea
              placeholder="Type your message..."
              value={smsBody}
              onChange={e => setSmsBody(e.target.value)}
              rows={3}
              className="resize-none"
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSmsVendor(null)}>Cancel</Button>
            <Button
              disabled={!smsBody.trim() || smsSending}
              className="gap-1.5"
              onClick={async () => {
                if (!smsVendor) return;
                setSmsSending(true);
                try {
                  await sendSms(smsVendor.id, smsBody.trim());
                  toast.success(`SMS sent to ${smsVendor.name}`);
                  setSmsVendor(null);
                  setSmsBody('');
                } catch (e) {
                  toast.error(e instanceof Error ? e.message : 'Failed to send SMS');
                } finally {
                  setSmsSending(false);
                }
              }}
            >
              {smsSending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
              Send SMS
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default Vendors;
