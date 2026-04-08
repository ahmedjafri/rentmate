import { useMemo, useState } from 'react';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Building2, Users, ChevronRight, Home, Plus, X, Loader2, FileText } from 'lucide-react';
import { PageLoader } from '@/components/ui/page-loader';
import { Link } from 'react-router-dom';
import { graphqlQuery } from '@/data/api';
import { CREATE_PROPERTY_MUTATION } from '@/data/api';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';

const sourceConfig = {
  manual:   { label: 'Manual',        className: 'bg-muted text-muted-foreground' },
  document: { label: 'From document', className: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' },
};

const Properties = () => {
  const { properties, tenants, actionDeskTasks, addProperty, isLoading } = useApp();
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);

  // Form state
  const [address, setAddress] = useState('');
  const [name, setName] = useState('');
  const [city, setCity] = useState('');
  const [state, setState] = useState('');
  const [postalCode, setPostalCode] = useState('');
  const [propertyType, setPropertyType] = useState<'multi_family' | 'single_family'>('multi_family');
  const [unitLabels, setUnitLabels] = useState<string[]>(['']);

  const resetForm = () => {
    setAddress(''); setName(''); setCity(''); setState('');
    setPostalCode(''); setPropertyType('multi_family'); setUnitLabels(['']);
    setShowForm(false);
  };

  const handleCreate = async () => {
    if (!address.trim()) { toast.error('Address is required'); return; }
    setSaving(true);
    try {
      const input: Record<string, unknown> = {
        address: address.trim(),
        propertyType,
        name: name.trim() || null,
        city: city.trim() || null,
        state: state.trim() || null,
        postalCode: postalCode.trim() || null,
      };
      if (propertyType === 'multi_family') {
        input.unitLabels = unitLabels.map(l => l.trim()).filter(Boolean);
      }
      const data = await graphqlQuery(CREATE_PROPERTY_MUTATION, { input });
      const p = (data as Record<string, unknown>).createProperty as Record<string, unknown>;
      addProperty({
        id: p.uid as string,
        name: (p.name || p.address) as string,
        address: p.address as string,
        propertyType: p.propertyType as 'single_family' | 'multi_family',
        source: p.source as 'manual' | 'document',
        units: (p.units as number) ?? 0,
        occupiedUnits: (p.occupiedUnits as number) ?? 0,
        monthlyRevenue: (p.monthlyRevenue as number) ?? 0,
        unitList: (p.unitList as { uid: string; label: string; isOccupied: boolean }[])?.map((u) => ({
          id: u.uid, label: u.label, isOccupied: u.isOccupied,
        })),
      });
      toast.success(`Property added: ${p.address as string}`);
      resetForm();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to create property');
    } finally {
      setSaving(false);
    }
  };

  const sortedProperties = useMemo(() => {
    return [...properties].sort((a, b) => {
      const aCount = actionDeskTasks.filter(t => t.propertyId === a.id && t.status === 'active').length;
      const bCount = actionDeskTasks.filter(t => t.propertyId === b.id && t.status === 'active').length;
      return bCount - aCount;
    });
  }, [properties, actionDeskTasks]);

  if (isLoading) return <PageLoader />;

  return (
    <div className="p-4 sm:p-6 max-w-4xl mx-auto space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Properties</h1>
          <p className="text-sm text-muted-foreground">{properties.length} properties under management</p>
        </div>
        <Button onClick={() => setShowForm(s => !s)} variant={showForm ? 'outline' : 'default'} className="gap-2 rounded-xl shrink-0">
          {showForm ? <><X className="h-4 w-4" /><span className="hidden sm:inline">Cancel</span></> : <><Plus className="h-4 w-4" /><span className="hidden sm:inline">Add Property</span><span className="sm:hidden">Add</span></>}
        </Button>
      </div>

      {/* Creation form */}
      {showForm && (
        <Card className="rounded-xl p-5 space-y-4 border-primary/30">
          <h2 className="text-sm font-semibold">New Property</h2>

          {/* Property type toggle */}
          <div className="flex gap-2">
            <button
              onClick={() => setPropertyType('multi_family')}
              className={cn('flex-1 flex items-center justify-center gap-1.5 rounded-lg border py-2 text-sm transition-colors',
                propertyType === 'multi_family' ? 'border-primary bg-primary/5 text-primary font-medium' : 'border-muted-foreground/20 text-muted-foreground hover:border-muted-foreground/40'
              )}
            >
              <Building2 className="h-4 w-4" /> Multi-Family
            </button>
            <button
              onClick={() => setPropertyType('single_family')}
              className={cn('flex-1 flex items-center justify-center gap-1.5 rounded-lg border py-2 text-sm transition-colors',
                propertyType === 'single_family' ? 'border-primary bg-primary/5 text-primary font-medium' : 'border-muted-foreground/20 text-muted-foreground hover:border-muted-foreground/40'
              )}
            >
              <Home className="h-4 w-4" /> Single Family
            </button>
          </div>

          {/* Address fields */}
          <div className="space-y-2">
            <Input
              placeholder="Street address *"
              value={address}
              onChange={e => setAddress(e.target.value)}
              className="rounded-lg"
            />
            <Input
              placeholder="Name / nickname (optional)"
              value={name}
              onChange={e => setName(e.target.value)}
              className="rounded-lg"
            />
            <div className="grid grid-cols-3 gap-2">
              <Input placeholder="City" value={city} onChange={e => setCity(e.target.value)} className="rounded-lg col-span-2 sm:col-span-1" />
              <Input placeholder="State" value={state} onChange={e => setState(e.target.value)} className="rounded-lg" />
              <Input placeholder="ZIP" value={postalCode} onChange={e => setPostalCode(e.target.value)} className="rounded-lg col-span-3 sm:col-span-1" />
            </div>
          </div>

          {/* Units (multi-family only) */}
          {propertyType === 'multi_family' && (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground font-medium">Units <span className="font-normal">(optional — add labels like 1A, 2B, #101)</span></p>
              {unitLabels.map((label, i) => (
                <div key={i} className="flex gap-2">
                  <Input
                    placeholder={`Unit ${i + 1}`}
                    value={label}
                    onChange={e => setUnitLabels(prev => prev.map((l, j) => j === i ? e.target.value : l))}
                    className="rounded-lg"
                  />
                  {unitLabels.length > 1 && (
                    <Button variant="ghost" size="icon" className="shrink-0 h-9 w-9 rounded-lg text-muted-foreground hover:text-destructive"
                      onClick={() => setUnitLabels(prev => prev.filter((_, j) => j !== i))}>
                      <X className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              ))}
              <Button variant="ghost" size="sm" className="gap-1.5 text-xs rounded-lg"
                onClick={() => setUnitLabels(prev => [...prev, ''])}>
                <Plus className="h-3.5 w-3.5" /> Add unit
              </Button>
            </div>
          )}

          <div className="flex gap-2 pt-1">
            <Button onClick={handleCreate} disabled={saving || !address.trim()} className="rounded-lg gap-2">
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              Create property
            </Button>
            <Button variant="ghost" onClick={resetForm} className="rounded-lg">Cancel</Button>
          </div>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {sortedProperties.map((property) => {
          const propertyTenants = tenants.filter(t => t.propertyId === property.id);
          const activeTenantCount = propertyTenants.filter(t => t.isActive).length;
          const openTaskCount = actionDeskTasks.filter(t => t.propertyId === property.id && t.status === 'active').length;
          const isSingleFamily = property.propertyType === 'single_family';
          const src = property.source ?? 'manual';
          const srcCfg = sourceConfig[src as keyof typeof sourceConfig] ?? sourceConfig.manual;

          return (
            <Link key={property.id} to={`/properties/${property.id}`} className="block min-w-0">
              <Card className="p-5 rounded-xl hover:shadow-md transition-shadow cursor-pointer overflow-hidden">
                <div className="flex items-start justify-between mb-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-semibold text-base truncate">{property.name || property.address}</h3>
                      <Badge variant="secondary" className="text-[10px] rounded-md gap-1 shrink-0">
                        {isSingleFamily ? <Home className="h-2.5 w-2.5" /> : <Building2 className="h-2.5 w-2.5" />}
                        {isSingleFamily ? 'Single Family' : 'Multi-Family'}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <p className="text-sm text-muted-foreground truncate">{property.address}</p>
                      <Badge variant="secondary" className={cn('text-[10px] rounded-md shrink-0', srcCfg.className)}>
                        {src === 'document' ? <FileText className="h-2.5 w-2.5 mr-1" /> : null}
                        {srcCfg.label}
                      </Badge>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-2">
                    {openTaskCount > 0 && (
                      <Badge variant="secondary" className="rounded-lg bg-warning/15 text-warning-foreground">
                        {openTaskCount} {openTaskCount === 1 ? 'task' : 'tasks'}
                      </Badge>
                    )}
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  </div>
                </div>

                <div className="flex items-center gap-4 mt-4">
                  {!isSingleFamily && (
                    <div className="flex items-center gap-2">
                      <Building2 className="h-4 w-4 text-muted-foreground" />
                      <div>
                        <p className="text-sm font-medium">{property.occupiedUnits}/{property.units}</p>
                        <p className="text-[11px] text-muted-foreground">Units</p>
                      </div>
                    </div>
                  )}
                  <div className="flex items-center gap-2">
                    <Users className="h-4 w-4 text-muted-foreground" />
                    <div>
                      <p className="text-sm font-medium">{activeTenantCount}/{propertyTenants.length}</p>
                      <p className="text-[11px] text-muted-foreground">Tenants</p>
                    </div>
                  </div>
                </div>
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
};

export default Properties;
