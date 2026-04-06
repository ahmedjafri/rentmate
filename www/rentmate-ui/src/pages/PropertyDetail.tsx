import { useParams, Link, useNavigate } from 'react-router-dom';
import { useState, useEffect } from 'react';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EntityContextCard, propertyTopics } from '@/components/context/EntityContextCard';
import { Users, ArrowLeft, MapPin, Bot, Wrench, User, Clock, MessageCircle, Zap, ShieldCheck, Hand, Lock, ChevronRight, Building2, Home, FileText, Trash2, Plus, X, Loader2, Pencil } from 'lucide-react';
import { graphqlQuery, DELETE_PROPERTY_MUTATION, UPDATE_PROPERTY_MUTATION, CREATE_TENANT_WITH_LEASE_MUTATION, ADD_LEASE_FOR_TENANT_MUTATION } from '@/data/api';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { authFetch } from '@/lib/auth';
import { toast } from 'sonner';
import { formatDistanceToNow } from 'date-fns';
import { TaskMode, TaskParticipantType, categoryColors, categoryLabels } from '@/data/mockData';
import { cn } from '@/lib/utils';

const modeConfig: Record<TaskMode, { label: string; icon: React.ElementType; className: string }> = {
  autonomous: { label: 'Autonomous', icon: Zap, className: 'bg-accent/15 text-accent' },
  waiting_approval: { label: 'Needs Approval', icon: ShieldCheck, className: 'bg-warning/15 text-warning-foreground' },
  manual: { label: 'Manual', icon: Hand, className: 'bg-muted text-muted-foreground' },
};

const participantIcon: Record<TaskParticipantType, React.ElementType> = {
  agent: Bot,
  tenant: User,
  vendor: Wrench,
  manager: User,
};

const PropertyDetail = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { properties, tenants, actionDeskTasks, openChat, removeProperty, updateProperty, addTenant } = useApp();
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editForm, setEditForm] = useState({ name: '', address: '', propertyType: '' });
  const [linkedDocs, setLinkedDocs] = useState<{ id: string; filename: string; status: string; created_at: string | null }[]>([]);
  const [showAddTenant, setShowAddTenant] = useState(false);
  const [addingTenant, setAddingTenant] = useState(false);
  const [addMode, setAddMode] = useState<'new' | 'existing'>('new');
  const [tenantSearch, setTenantSearch] = useState('');
  const [selectedExistingId, setSelectedExistingId] = useState('');
  const [tenantForm, setTenantForm] = useState({
    firstName: '', lastName: '', email: '', phone: '',
    unitId: '', leaseStart: '', leaseEnd: '', rentAmount: '',
  });

  useEffect(() => {
    if (!id) return;
    authFetch(`/api/properties/${id}/documents`)
      .then(r => r.ok ? r.json() : [])
      .then(setLinkedDocs)
      .catch(() => {});
  }, [id]);

  const handleDelete = async () => {
    if (!confirmDelete) { setConfirmDelete(true); return; }
    setDeleting(true);
    try {
      await graphqlQuery(DELETE_PROPERTY_MUTATION, { uid: id });
      removeProperty(id!);
      toast.success('Property deleted');
      navigate('/properties');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete property');
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  const handleEditOpen = () => {
    if (!property) return;
    setEditForm({ name: property.name || '', address: property.address || '', propertyType: property.propertyType || 'multi_family' });
    setShowEdit(true);
  };

  const handleSaveEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id) return;
    setSaving(true);
    try {
      await graphqlQuery(UPDATE_PROPERTY_MUTATION, {
        input: {
          uid: id,
          name: editForm.name || null,
          address: editForm.address || null,
          propertyType: editForm.propertyType || null,
        },
      });
      updateProperty(id, {
        name: editForm.name,
        address: editForm.address,
        propertyType: editForm.propertyType as 'single_family' | 'multi_family',
      });
      toast.success('Property updated');
      setShowEdit(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to update property');
    } finally {
      setSaving(false);
    }
  };

  type TenantResult = { uid: string; name: string; email: string | null; unitLabel: string | null; leaseEndDate: string | null; rentAmount: number | null; paymentStatus: string | null; isActive: boolean };

  const handleAddTenant = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id) return;
    setAddingTenant(true);
    try {
      let t: TenantResult;
      if (addMode === 'existing') {
        if (!selectedExistingId) { toast.error('Select a tenant'); return; }
        const result = await graphqlQuery<{ addLeaseForTenant: TenantResult }>(
          ADD_LEASE_FOR_TENANT_MUTATION,
          {
            input: {
              tenantId: selectedExistingId,
              propertyId: id,
              unitId: tenantForm.unitId,
              leaseStart: tenantForm.leaseStart,
              leaseEnd: tenantForm.leaseEnd,
              rentAmount: parseFloat(tenantForm.rentAmount),
            },
          }
        );
        t = result.addLeaseForTenant;
      } else {
        const result = await graphqlQuery<{ createTenantWithLease: TenantResult }>(
          CREATE_TENANT_WITH_LEASE_MUTATION,
          {
            input: {
              firstName: tenantForm.firstName,
              lastName: tenantForm.lastName,
              email: tenantForm.email || null,
              phone: tenantForm.phone || null,
              propertyId: id,
              unitId: tenantForm.unitId,
              leaseStart: tenantForm.leaseStart,
              leaseEnd: tenantForm.leaseEnd,
              rentAmount: parseFloat(tenantForm.rentAmount),
            },
          }
        );
        t = result.createTenantWithLease;
      }
      addTenant({
        id: t.uid,
        name: t.name,
        email: t.email ?? '',
        unit: t.unitLabel ?? '',
        propertyId: id,
        leaseEnd: t.leaseEndDate ? new Date(t.leaseEndDate) : new Date(),
        rentAmount: t.rentAmount ?? 0,
        paymentStatus: (t.paymentStatus as 'current' | 'late' | 'overdue') ?? 'current',
        isActive: true,
      });
      toast.success('Tenant added');
      setShowAddTenant(false);
      setAddMode('new');
      setSelectedExistingId('');
      setTenantSearch('');
      setTenantForm({ firstName: '', lastName: '', email: '', phone: '', unitId: '', leaseStart: '', leaseEnd: '', rentAmount: '' });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to add tenant');
    } finally {
      setAddingTenant(false);
    }
  };

  const property = properties.find(p => p.id === id);
  if (!property) {
    return (
      <div className="p-6 max-w-4xl mx-auto">
        <Link to="/properties" className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-4">
          <ArrowLeft className="h-4 w-4" /> Back to Properties
        </Link>
        <p className="text-muted-foreground">Property not found.</p>
      </div>
    );
  }

  const propertyTenants = tenants.filter(t => t.propertyId === property.id);
  const currentTenants = propertyTenants.filter(t => t.isActive);
  const pastTenants = propertyTenants.filter(t => !t.isActive);
  const propertyTasks = actionDeskTasks.filter(t => t.propertyId === property.id && t.status === 'active');
  const isSingleFamily = property.propertyType === 'single_family';
  const occupancyRate = isSingleFamily ? 0 : Math.round((property.occupiedUnits / (property.units || 1)) * 100);

  const vacantUnits = property.units - property.occupiedUnits;
  const lateTenants = currentTenants.filter(t => t.paymentStatus === 'late' || t.paymentStatus === 'overdue');
  const upcomingLeases = currentTenants.filter(t => {
    const months = (t.leaseEnd.getTime() - Date.now()) / (1000 * 60 * 60 * 24 * 30);
    return months <= 3 && months > 0;
  });

  const autoContext = [
    { label: 'Property', value: property.name || property.address },
    { label: 'Address', value: property.address },
    { label: 'Type', value: isSingleFamily ? 'Single Family' : 'Multi-Family' },
    ...(!isSingleFamily ? [{ label: 'Units', value: `${property.occupiedUnits}/${property.units} occupied (${occupancyRate}%)${vacantUnits > 0 ? ` · ${vacantUnits} vacant` : ''}` }] : []),
    { label: 'Tenants', value: currentTenants.length > 0 ? currentTenants.map(t => `${t.name} (Unit ${t.unit})`).join(', ') : 'None' },
    ...(lateTenants.length > 0 ? [{ label: 'Payment issues', value: lateTenants.map(t => `${t.name} — ${t.paymentStatus}`).join(', ') }] : []),
    ...(upcomingLeases.length > 0 ? [{ label: 'Leases expiring soon', value: upcomingLeases.map(t => `${t.name} (${t.leaseEnd.toLocaleDateString()})`).join(', ') }] : []),
    { label: 'Open tasks', value: `${propertyTasks.length}` },
  ];

  return (
    <div className="p-4 sm:p-6 max-w-4xl mx-auto space-y-4">
      {/* Header: back link + actions row, then title row */}
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <Link to="/properties" className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground shrink-0">
            <ArrowLeft className="h-4 w-4" /> Back to Properties
          </Link>
          <div className="flex items-center gap-1.5 shrink-0">
            <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-xs" onClick={handleEditOpen}>
              <Pencil className="h-3.5 w-3.5" />
              Edit
            </Button>
            <Button
              variant={confirmDelete ? 'destructive' : 'ghost'}
              size="sm"
              className="h-7 gap-1.5 text-xs"
              disabled={deleting}
              onClick={handleDelete}
              onBlur={() => setConfirmDelete(false)}
            >
              <Trash2 className="h-3.5 w-3.5" />
              {confirmDelete ? 'Confirm' : 'Delete'}
            </Button>
          </div>
        </div>
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-lg font-semibold">{property.name || property.address}</h1>
            <Badge variant="secondary" className="text-[10px] rounded-md gap-1">
              {isSingleFamily ? <Home className="h-2.5 w-2.5" /> : <Building2 className="h-2.5 w-2.5" />}
              {isSingleFamily ? 'Single Family' : 'Multi-Family'}
            </Badge>
            <Badge variant="secondary" className={cn('text-[10px] rounded-md gap-1',
              property.source === 'document'
                ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                : 'bg-muted text-muted-foreground'
            )}>
              {property.source === 'document' ? <FileText className="h-2.5 w-2.5" /> : null}
              {property.source === 'document' ? 'From document' : 'Manual'}
            </Badge>
          </div>
          <div className="flex items-center gap-1 text-xs text-muted-foreground mt-0.5">
            <MapPin className="h-3 w-3" />
            {property.address}
          </div>
        </div>
      </div>

      {/* Edit form */}
      {showEdit && (
        <Card className="p-4 rounded-xl">
          <form onSubmit={handleSaveEdit} className="space-y-3">
            <div className="space-y-1">
              <Label className="text-xs">Property Name</Label>
              <Input className="h-8 text-sm rounded-lg" value={editForm.name} onChange={e => setEditForm(f => ({ ...f, name: e.target.value }))} placeholder="e.g. Sunset Apartments" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Address</Label>
              <Input className="h-8 text-sm rounded-lg" value={editForm.address} onChange={e => setEditForm(f => ({ ...f, address: e.target.value }))} placeholder="123 Main St" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Property Type</Label>
              <Select value={editForm.propertyType} onValueChange={v => setEditForm(f => ({ ...f, propertyType: v }))}>
                <SelectTrigger className="h-8 text-sm rounded-lg">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="multi_family">Multi-Family</SelectItem>
                  <SelectItem value="single_family">Single Family</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" size="sm" className="text-xs rounded-lg" onClick={() => setShowEdit(false)}>Cancel</Button>
              <Button type="submit" size="sm" className="text-xs rounded-lg gap-1.5" disabled={saving}>
                {saving && <Loader2 className="h-3 w-3 animate-spin" />}
                Save
              </Button>
            </div>
          </form>
        </Card>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 gap-3">
        <EntityContextCard entityId={property.id} entityName={property.name || property.address} entityType="property" agentContext={property.context} onAgentContextSaved={(ctx) => updateProperty(property.id, { context: ctx })} expectedTopics={propertyTopics} autoContext={autoContext} />
        <Card className="p-4 rounded-xl">
          <div className="flex items-center gap-2 mb-1">
            <Users className="h-4 w-4 text-muted-foreground" />
            <span className="text-xs text-muted-foreground">Tenants</span>
          </div>
          <p className="text-xl font-bold">{currentTenants.length}</p>
          <p className="text-[11px] text-muted-foreground">active leases</p>
        </Card>
      </div>

      {/* Units — only shown for multi-family */}
      {!isSingleFamily && property.unitList && property.unitList.length > 0 && (
        <div>
          <h2 className="text-sm font-bold mb-2">Units</h2>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {property.unitList.map(u => (
              <Card key={u.id} className="p-3 rounded-xl flex items-center gap-2">
                <Building2 className="h-4 w-4 text-muted-foreground shrink-0" />
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate">{u.label}</p>
                  <p className={`text-[11px] ${u.isOccupied ? 'text-green-600' : 'text-muted-foreground'}`}>
                    {u.isOccupied ? 'Occupied' : 'Vacant'}
                  </p>
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* Tenants */}
      <div className="space-y-4">
        <div>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-bold">Current Tenants</h2>
            <Button variant="ghost" size="sm" className="h-7 gap-1 text-xs" onClick={() => {
              const defaultUnit = property.unitList?.[0]?.id ?? '';
              setTenantForm(f => ({ ...f, unitId: defaultUnit }));
              setAddMode('new');
              setSelectedExistingId('');
              setTenantSearch('');
              setShowAddTenant(v => !v);
            }}>
              {showAddTenant ? <X className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
              {showAddTenant ? 'Cancel' : 'Add Tenant'}
            </Button>
          </div>

          {showAddTenant && (() => {
            const existingCandidates = tenants.filter(t =>
              t.propertyId !== id &&
              (tenantSearch.trim() === '' ||
                t.name.toLowerCase().includes(tenantSearch.toLowerCase()) ||
                t.email.toLowerCase().includes(tenantSearch.toLowerCase()))
            );
            const selectedTenant = tenants.find(t => t.id === selectedExistingId);
            const leaseFields = (
              <>
                {!isSingleFamily && property.unitList && property.unitList.length > 0 && (
                  <div className="space-y-1">
                    <Label className="text-xs">Unit *</Label>
                    <Select value={tenantForm.unitId} onValueChange={v => setTenantForm(f => ({ ...f, unitId: v }))}>
                      <SelectTrigger className="h-8 text-sm rounded-lg">
                        <SelectValue placeholder="Select unit" />
                      </SelectTrigger>
                      <SelectContent>
                        {property.unitList.map(u => (
                          <SelectItem key={u.id} value={u.id}>{u.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <Label className="text-xs">Lease Start *</Label>
                    <Input className="h-8 text-sm rounded-lg" type="date" value={tenantForm.leaseStart} onChange={e => setTenantForm(f => ({ ...f, leaseStart: e.target.value }))} required />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">Lease End *</Label>
                    <Input className="h-8 text-sm rounded-lg" type="date" value={tenantForm.leaseEnd} onChange={e => setTenantForm(f => ({ ...f, leaseEnd: e.target.value }))} required />
                  </div>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Monthly Rent ($) *</Label>
                  <Input className="h-8 text-sm rounded-lg" type="number" min="0" step="0.01" value={tenantForm.rentAmount} onChange={e => setTenantForm(f => ({ ...f, rentAmount: e.target.value }))} required />
                </div>
              </>
            );
            return (
              <Card className="p-4 rounded-xl mb-3">
                {/* Mode toggle */}
                <div className="flex rounded-lg border overflow-hidden text-xs mb-3">
                  {(['new', 'existing'] as const).map(m => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => { setAddMode(m); setSelectedExistingId(''); setTenantSearch(''); }}
                      className={`flex-1 py-1.5 capitalize transition-colors ${addMode === m ? 'bg-primary text-primary-foreground font-medium' : 'hover:bg-muted/50 text-muted-foreground'}`}
                    >
                      {m === 'new' ? 'New Tenant' : 'Existing Tenant'}
                    </button>
                  ))}
                </div>

                <form onSubmit={handleAddTenant} className="space-y-3">
                  {addMode === 'new' ? (
                    <>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div className="space-y-1">
                          <Label className="text-xs">First Name *</Label>
                          <Input className="h-8 text-sm rounded-lg" value={tenantForm.firstName} onChange={e => setTenantForm(f => ({ ...f, firstName: e.target.value }))} required />
                        </div>
                        <div className="space-y-1">
                          <Label className="text-xs">Last Name *</Label>
                          <Input className="h-8 text-sm rounded-lg" value={tenantForm.lastName} onChange={e => setTenantForm(f => ({ ...f, lastName: e.target.value }))} required />
                        </div>
                      </div>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div className="space-y-1">
                          <Label className="text-xs">Email</Label>
                          <Input className="h-8 text-sm rounded-lg" type="email" value={tenantForm.email} onChange={e => setTenantForm(f => ({ ...f, email: e.target.value }))} />
                        </div>
                        <div className="space-y-1">
                          <Label className="text-xs">Phone</Label>
                          <Input className="h-8 text-sm rounded-lg" type="tel" value={tenantForm.phone} onChange={e => setTenantForm(f => ({ ...f, phone: e.target.value }))} />
                        </div>
                      </div>
                      {leaseFields}
                    </>
                  ) : (
                    <>
                      <div className="space-y-1">
                        <Label className="text-xs">Select Tenant *</Label>
                        {selectedTenant ? (
                          <div className="flex items-center justify-between h-8 px-3 rounded-lg border bg-muted/40 text-sm">
                            <span className="font-medium">{selectedTenant.name}</span>
                            <button type="button" onClick={() => { setSelectedExistingId(''); setTenantSearch(''); }} className="text-muted-foreground hover:text-foreground">
                              <X className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        ) : (
                          <>
                            <Input
                              className="h-8 text-sm rounded-lg"
                              placeholder="Search by name or email…"
                              value={tenantSearch}
                              onChange={e => setTenantSearch(e.target.value)}
                            />
                            {tenantSearch.trim() !== '' && (
                              <div className="rounded-lg border bg-card shadow-sm max-h-40 overflow-y-auto">
                                {existingCandidates.length === 0 ? (
                                  <p className="text-xs text-muted-foreground px-3 py-2">No tenants found</p>
                                ) : (
                                  existingCandidates.map(t => (
                                    <button
                                      key={t.id}
                                      type="button"
                                      onClick={() => { setSelectedExistingId(t.id); setTenantSearch(''); }}
                                      className="w-full text-left px-3 py-2 text-sm hover:bg-muted/50 flex items-center justify-between"
                                    >
                                      <span className="font-medium">{t.name}</span>
                                      <span className="text-xs text-muted-foreground">{t.email}</span>
                                    </button>
                                  ))
                                )}
                              </div>
                            )}
                          </>
                        )}
                      </div>
                      {leaseFields}
                    </>
                  )}
                  <div className="flex justify-end">
                    <Button type="submit" size="sm" className="rounded-lg gap-1.5 text-xs" disabled={addingTenant || (addMode === 'existing' && !selectedExistingId)}>
                      {addingTenant && <Loader2 className="h-3 w-3 animate-spin" />}
                      Add Tenant
                    </Button>
                  </div>
                </form>
              </Card>
            );
          })()}

          {currentTenants.length === 0 && !showAddTenant ? (
            <p className="text-sm text-muted-foreground">No current tenants.</p>
          ) : currentTenants.length > 0 ? (
            <div className="space-y-2">
              {currentTenants.map(t => (
                <Link key={t.id} to={`/tenants/${t.id}`} state={{ from: 'property', propertyId: property.id, propertyName: property.name || property.address }}>
                  <Card className="p-4 rounded-xl flex items-center justify-between hover:shadow-md transition-shadow cursor-pointer">
                    <div>
                      <p className="text-sm font-medium">{t.name}</p>
                      <p className="text-xs text-muted-foreground">Unit {t.unit} · Lease ends {t.leaseEnd.toLocaleDateString()}</p>
                    </div>
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  </Card>
                </Link>
              ))}
            </div>
          ) : null}
        </div>

        {pastTenants.length > 0 && (
          <div>
            <h2 className="text-sm font-bold mb-2 text-muted-foreground">Past Tenants</h2>
            <div className="space-y-2">
              {pastTenants.map(t => (
                <Link key={t.id} to={`/tenants/${t.id}`} state={{ from: 'property', propertyId: property.id, propertyName: property.name || property.address }}>
                  <Card className="p-4 rounded-xl flex items-center justify-between hover:shadow-md transition-shadow cursor-pointer opacity-60">
                    <div>
                      <p className="text-sm font-medium">{t.name}</p>
                      <p className="text-xs text-muted-foreground">Unit {t.unit} · Lease ended {t.leaseEnd.toLocaleDateString()}</p>
                    </div>
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  </Card>
                </Link>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Documents */}
      {linkedDocs.length > 0 && (
        <div>
          <h2 className="text-sm font-bold mb-2">Documents</h2>
          <div className="space-y-2">
            {linkedDocs.map(doc => (
              <Link key={doc.id} to={`/documents/${doc.id}`}>
                <Card className="p-3 rounded-xl flex items-center gap-3 hover:shadow-md transition-shadow cursor-pointer">
                  <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium truncate">{doc.filename}</p>
                    {doc.created_at && (
                      <p className="text-[11px] text-muted-foreground">
                        {formatDistanceToNow(new Date(doc.created_at), { addSuffix: true })}
                      </p>
                    )}
                  </div>
                  <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" />
                </Card>
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* Open Tasks */}
      {propertyTasks.length > 0 && (
        <div>
          <h2 className="text-sm font-bold mb-2">Open Tasks</h2>
          <div className="space-y-3">
            {propertyTasks.map(task => {
              const mode = modeConfig[task.mode];
              const ModeIcon = mode.icon;

              return (
                <Card key={task.id} className="p-4 rounded-xl hover:shadow-md transition-shadow cursor-pointer" onClick={() => openChat({ taskId: task.id })}>
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Badge variant="secondary" className={cn('text-[10px] rounded-lg gap-1', mode.className)}>
                        <ModeIcon className="h-3 w-3" />
                        {mode.label}
                      </Badge>
                      <Badge variant="secondary" className={cn('text-[10px] rounded-lg', categoryColors[task.category])}>
                        {categoryLabels[task.category]}
                      </Badge>
                      {task.unreadCount > 0 && (
                        <Badge className="h-4 px-1.5 text-[10px] bg-primary text-primary-foreground">
                          {task.unreadCount} new
                        </Badge>
                      )}
                      {task.confidential && (
                        <Badge variant="secondary" className="text-[10px] rounded-lg gap-1 bg-destructive/10 text-destructive">
                          <Lock className="h-3 w-3" />
                          Confidential
                        </Badge>
                      )}
                    </div>
                    <div className="flex items-center gap-1 text-[11px] text-muted-foreground shrink-0">
                      <Clock className="h-3 w-3" />
                      {formatDistanceToNow(new Date(task.lastMessageAt), { addSuffix: true })}
                    </div>
                  </div>

                  <h3 className="font-semibold text-sm mb-1">{task.title}</h3>

                  <div className="flex items-start gap-2 mt-2 bg-muted/40 rounded-lg p-2.5">
                    <MessageCircle className="h-3 w-3 text-muted-foreground mt-0.5 shrink-0" />
                    <div className="min-w-0">
                      <span className="text-[11px] font-medium text-muted-foreground">{task.lastMessageBy}</span>
                      <p className="text-xs text-foreground line-clamp-2">{task.lastMessage}</p>
                    </div>
                  </div>

                  <div className="mt-2.5 flex items-center gap-1">
                    {task.participants.map((p, i) => {
                      const Icon = participantIcon[p.type];
                      return (
                        <div
                          key={i}
                          className={cn(
                            'flex h-5 w-5 items-center justify-center rounded-full text-[10px]',
                            p.type === 'agent' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground'
                          )}
                          title={`${p.name} (${p.type})`}
                        >
                          <Icon className="h-3 w-3" />
                        </div>
                      );
                    })}
                    <span className="text-[10px] text-muted-foreground ml-1">
                      {task.participants.map(p => p.name.split(' ')[0]).join(', ')}
                    </span>
                  </div>
                </Card>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

export default PropertyDetail;
