import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Users, Search, ChevronRight, CalendarDays, DollarSign, Trash2 } from 'lucide-react';
import { PageLoader } from '@/components/ui/page-loader';
import { graphqlQuery, DELETE_TENANT_MUTATION } from '@/data/api';
import { toast } from 'sonner';

const paymentConfig = {
  current:  { label: 'Current',  className: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' },
  late:     { label: 'Late',     className: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400' },
  overdue:  { label: 'Overdue', className: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' },
};

const Tenants = () => {
  const { tenants, properties, isLoading, removeTenant } = useApp();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'all' | 'active' | 'inactive'>('all');

  const propertyMap = useMemo(() =>
    Object.fromEntries(properties.map(p => [p.id, p])),
    [properties]
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return tenants.filter(t => {
      if (filter === 'active' && !t.isActive) return false;
      if (filter === 'inactive' && t.isActive) return false;
      if (!q) return true;
      return (
        t.name.toLowerCase().includes(q) ||
        t.email.toLowerCase().includes(q) ||
        t.unit?.toLowerCase().includes(q) ||
        propertyMap[t.propertyId]?.address?.toLowerCase().includes(q)
      );
    });
  }, [tenants, filter, search, propertyMap]);

  const activeCount = tenants.filter(t => t.isActive).length;
  const inactiveCount = tenants.length - activeCount;

  if (isLoading) return <PageLoader />;

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">Tenants</h1>
        <p className="text-sm text-muted-foreground">
          {activeCount} active · {inactiveCount} inactive
        </p>
      </div>

      {/* Search + filter */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search by name, email, or unit…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9 rounded-xl"
          />
        </div>
        <div className="flex rounded-xl border overflow-hidden text-sm shrink-0">
          {(['all', 'active', 'inactive'] as const).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-4 py-2 capitalize transition-colors ${
                filter === f
                  ? 'bg-primary text-primary-foreground font-medium'
                  : 'hover:bg-muted/50 text-muted-foreground'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {/* List */}
      {filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center text-muted-foreground gap-3">
          <Users className="h-10 w-10 opacity-30" />
          <p className="text-sm">No tenants found</p>
        </div>
      ) : (
        <div className="grid gap-3">
          {filtered.map(tenant => {
            const property = propertyMap[tenant.propertyId];
            const payStatus = tenant.paymentStatus ?? 'current';
            const payCfg = paymentConfig[payStatus] ?? paymentConfig.current;
            const leaseEnd = tenant.leaseEnd ? new Date(tenant.leaseEnd) : null;
            const daysLeft = leaseEnd
              ? Math.ceil((leaseEnd.getTime() - Date.now()) / 86400000)
              : null;

            return (
              <div key={tenant.id} className="relative group">
                <Link to={`/tenants/${tenant.id}`} state={{ from: 'tenants' }}>
                  <Card className="p-4 rounded-xl hover:shadow-md transition-shadow cursor-pointer">
                    <div className="flex items-center gap-4">
                      {/* Avatar */}
                      <div className="h-10 w-10 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <span className="text-sm font-semibold text-primary">
                          {tenant.name.charAt(0).toUpperCase()}
                        </span>
                      </div>

                      {/* Main info */}
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-medium truncate">{tenant.name}</span>
                          {!tenant.isActive && (
                            <Badge variant="secondary" className="text-[10px] rounded-md">Inactive</Badge>
                          )}
                          <Badge className={`text-[10px] rounded-md ${payCfg.className}`}>
                            {payCfg.label}
                          </Badge>
                        </div>
                        <p className="text-sm text-muted-foreground truncate">{tenant.email}</p>
                      </div>

                      {/* Meta */}
                      <div className="hidden sm:flex flex-col items-end gap-1 shrink-0 text-right">
                        {tenant.unit && (
                          <span className="text-sm font-medium">
                            {property ? `${property.name || property.address} · ` : ''}{tenant.unit}
                          </span>
                        )}
                        <div className="flex items-center gap-3 text-xs text-muted-foreground">
                          {tenant.rentAmount > 0 && (
                            <span className="flex items-center gap-1">
                              <DollarSign className="h-3 w-3" />
                              {tenant.rentAmount.toLocaleString()}/mo
                            </span>
                          )}
                          {leaseEnd && (
                            <span className={`flex items-center gap-1 ${daysLeft !== null && daysLeft < 60 ? 'text-yellow-600 dark:text-yellow-400' : ''}`}>
                              <CalendarDays className="h-3 w-3" />
                              {daysLeft !== null && daysLeft < 0
                                ? `Expired ${Math.abs(daysLeft)}d ago`
                                : `Ends ${leaseEnd.toLocaleDateString()}`}
                            </span>
                          )}
                        </div>
                      </div>

                      <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" />
                    </div>
                  </Card>
                </Link>
                <button
                  onClick={async (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    if (!confirm(`Delete tenant ${tenant.name}? This will also remove their leases.`)) return;
                    try {
                      await graphqlQuery(DELETE_TENANT_MUTATION, { uid: tenant.id });
                      removeTenant(tenant.id);
                      toast.success(`${tenant.name} deleted`);
                    } catch {
                      toast.error('Failed to delete tenant');
                    }
                  }}
                  className="absolute top-3 right-3 h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors hidden group-hover:flex"
                  title="Delete tenant"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default Tenants;
