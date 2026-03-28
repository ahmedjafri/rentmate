import { useEffect, useMemo, useState } from 'react';
import { useApp } from '@/context/AppContext';
import { Vendor } from '@/data/mockData';
import { graphqlQuery, CREATE_VENDOR_MUTATION, UPDATE_VENDOR_MUTATION, DELETE_VENDOR_MUTATION, VENDOR_TYPES_QUERY } from '@/data/api';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { PageLoader } from '@/components/ui/page-loader';
import {
  Dialog,
  DialogContent,
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
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Wrench, Plus, Pencil, Trash2, Search, MessageSquare, Mail, Phone } from 'lucide-react';
import { toast } from 'sonner';


interface FormState {
  name: string;
  company: string;
  vendorType: string;
  phone: string;
  email: string;
  notes: string;
  contactMethod: string;
}

const emptyForm = (): FormState => ({
  name: '', company: '', vendorType: '', phone: '', email: '', notes: '',
  contactMethod: 'rentmate',
});

const CONTACT_METHOD_LABELS: Record<string, { label: string; icon: React.ReactNode; disabled?: boolean; hint?: string }> = {
  rentmate: { label: 'RentMate', icon: <MessageSquare className="h-3.5 w-3.5" /> },
  email: { label: 'Email', icon: <Mail className="h-3.5 w-3.5" />, disabled: true, hint: 'Coming soon' },
  phone: { label: 'Phone / SMS', icon: <Phone className="h-3.5 w-3.5" />, disabled: true, hint: 'Coming soon' },
};

const contactMethodLabel = (method: string) =>
  CONTACT_METHOD_LABELS[method]?.label ?? method;

const Vendors = () => {
  const { vendors, isLoading, addVendor, updateVendor, removeVendor } = useApp();
  const [vendorTypes, setVendorTypes] = useState<string[]>([]);

  useEffect(() => {
    graphqlQuery<{ vendorTypes: string[] }>(VENDOR_TYPES_QUERY)
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
      contactMethod: v.contactMethod ?? 'rentmate',
    });
    setDialogOpen(true);
  };

  const handleSubmit = async () => {
    if (!form.name.trim()) {
      toast.error('Name is required');
      return;
    }
    setSaving(true);
    try {
      const input = {
        name: form.name.trim(),
        company: form.company.trim() || null,
        vendorType: form.vendorType || null,
        phone: form.phone.trim() || null,
        email: form.email.trim() || null,
        notes: form.notes.trim() || null,
        contactMethod: form.contactMethod,
      };

      if (editingId) {
        const data = await graphqlQuery<{ updateVendor: { uid: string; name: string; company?: string; vendorType?: string; phone?: string; email?: string; notes?: string; contactMethod?: string } }>(
          UPDATE_VENDOR_MUTATION,
          { input: { uid: editingId, ...input } },
        );
        updateVendor(editingId, {
          name: data.updateVendor.name,
          company: data.updateVendor.company,
          vendorType: data.updateVendor.vendorType,
          phone: data.updateVendor.phone,
          email: data.updateVendor.email,
          notes: data.updateVendor.notes,
          contactMethod: data.updateVendor.contactMethod ?? 'rentmate',
        });
        toast.success('Vendor updated');
      } else {
        const data = await graphqlQuery<{ createVendor: { uid: string; name: string; company?: string; vendorType?: string; phone?: string; email?: string; notes?: string; contactMethod?: string } }>(
          CREATE_VENDOR_MUTATION,
          { input },
        );
        addVendor({
          id: data.createVendor.uid,
          name: data.createVendor.name,
          company: data.createVendor.company,
          vendorType: data.createVendor.vendorType,
          phone: data.createVendor.phone,
          email: data.createVendor.email,
          notes: data.createVendor.notes,
          contactMethod: data.createVendor.contactMethod ?? 'rentmate',
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
      await graphqlQuery(DELETE_VENDOR_MUTATION, { uid: id });
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
                  <Badge variant="outline" className="text-xs gap-1">
                    {CONTACT_METHOD_LABELS[v.contactMethod ?? 'rentmate']?.icon}
                    {contactMethodLabel(v.contactMethod ?? 'rentmate')}
                  </Badge>
                </div>
                <div className="pt-1 space-y-0.5 text-sm text-muted-foreground">
                  {v.phone && <div>{v.phone}</div>}
                  {v.email && <div>{v.email}</div>}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Add / Edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{editingId ? 'Edit Vendor' : 'Add Vendor'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
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
              <Label>Vendor Type</Label>
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
              <Label>Contact Method</Label>
              <RadioGroup
                value={form.contactMethod}
                onValueChange={val => setForm(f => ({ ...f, contactMethod: val }))}
                className="flex flex-col gap-2"
              >
                {Object.entries(CONTACT_METHOD_LABELS).map(([value, { label, icon, disabled, hint }]) => (
                  <label
                    key={value}
                    className={`flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer transition-colors ${
                      disabled
                        ? 'opacity-40 cursor-not-allowed'
                        : form.contactMethod === value
                        ? 'border-primary bg-primary/5'
                        : 'hover:bg-muted/50'
                    }`}
                  >
                    <RadioGroupItem value={value} disabled={disabled} />
                    <span className="flex items-center gap-2 text-sm">
                      {icon}
                      {label}
                    </span>
                    {hint && (
                      <span className="ml-auto text-xs text-muted-foreground">{hint}</span>
                    )}
                  </label>
                ))}
              </RadioGroup>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="v-phone">Phone</Label>
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
    </div>
  );
};

export default Vendors;
