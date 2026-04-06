import { useState } from 'react';
import { useParams, Link, useLocation } from 'react-router-dom';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { EntityContextCard, propertyTopics } from '@/components/context/EntityContextCard';
import { ArrowLeft, MapPin, Bot, Wrench, User, Clock, MessageCircle, Zap, ShieldCheck, Hand, Lock, Mail, Calendar, Link as LinkIcon, Copy, CheckCircle2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
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

const tenantTopics = [
  { key: 'communication', label: 'Communication preferences', description: 'How they prefer to be contacted' },
  { key: 'history', label: 'Tenant history', description: 'Move-in date, previous issues, renewal history' },
  { key: 'note', label: 'Special notes', description: 'Pets, parking, accessibility needs, etc.' },
];

function TenantPortalLink({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      toast.success('Tenant portal link copied');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('Failed to copy');
    }
  };
  return (
    <Card className="p-4 rounded-xl">
      <div className="flex items-center gap-1.5 mb-1.5">
        <LinkIcon className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-xs text-muted-foreground">Tenant Portal Link</span>
      </div>
      <div className="flex items-center gap-1.5">
        <code className="text-[11px] bg-muted px-2 py-1 rounded truncate max-w-[400px] select-all">
          {url}
        </code>
        <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0" onClick={handleCopy}>
          {copied ? <CheckCircle2 className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5 text-muted-foreground" />}
        </Button>
      </div>
    </Card>
  );
}

const TenantDetail = () => {
  const { id } = useParams<{ id: string }>();
  const { state: navState } = useLocation();
  const { tenants, properties, actionDeskTasks, openChat, updateTenant } = useApp();

  const tenant = tenants.find(t => t.id === id);
  const backTo = navState?.from === 'tenants'
    ? { href: '/tenants', label: 'All Tenants' }
    : navState?.from === 'property'
      ? { href: `/properties/${navState.propertyId}`, label: navState.propertyName ?? 'Property' }
      : { href: '/tenants', label: 'All Tenants' };

  if (!tenant) {
    return (
      <div className="p-6 max-w-4xl mx-auto">
        <Link to={backTo.href} className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-4">
          <ArrowLeft className="h-4 w-4" /> Back to {backTo.label}
        </Link>
        <p className="text-muted-foreground">Tenant not found.</p>
      </div>
    );
  }

  const property = properties.find(p => p.id === tenant.propertyId);
  const tenantTasks = actionDeskTasks.filter(t => t.status === 'active' && t.chatThread.some(m => m.senderName?.includes(tenant.name.split(' ')[0])));
  const daysUntilLeaseEnd = Math.round((tenant.leaseEnd.getTime() - Date.now()) / (1000 * 60 * 60 * 24));

  const autoContext = [
    { label: 'Name', value: tenant.name },
    { label: 'Email', value: tenant.email },
    { label: 'Property', value: property?.name || 'Unknown' },
    { label: 'Unit', value: tenant.unit },
    { label: 'Rent', value: `$${tenant.rentAmount.toLocaleString()}/mo` },
    { label: 'Payment status', value: tenant.paymentStatus },
    { label: 'Lease ends', value: `${tenant.leaseEnd.toLocaleDateString()} (${daysUntilLeaseEnd > 0 ? `${daysUntilLeaseEnd} days` : 'expired'})` },
  ];

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <Link to={backTo.href} className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" /> Back to {backTo.label}
        </Link>
        <div className="text-right">
          <h1 className="text-base font-semibold">{tenant.name}</h1>
          <div className="flex items-center gap-1 text-xs text-muted-foreground">
            Unit {tenant.unit} · {property?.name}
          </div>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-3">
        <EntityContextCard entityId={tenant.id} entityName={tenant.name} entityType="tenant" agentContext={tenant.context} onAgentContextSaved={(ctx) => updateTenant(tenant.id, { context: ctx })} expectedTopics={tenantTopics} autoContext={autoContext} />
        <Card className="p-4 rounded-xl">
          <div className="flex items-center gap-2 mb-1">
            <Calendar className="h-4 w-4 text-muted-foreground" />
            <span className="text-xs text-muted-foreground">Lease</span>
          </div>
          {tenant.isActive ? (
            <>
              <p className="text-xl font-bold">{daysUntilLeaseEnd}</p>
              <p className="text-[11px] text-muted-foreground">days remaining</p>
            </>
          ) : (
            <>
              <p className="text-xl font-bold text-muted-foreground">Expired</p>
              <p className="text-[11px] text-muted-foreground">{tenant.leaseEnd.toLocaleDateString()}</p>
            </>
          )}
        </Card>
      </div>

      {/* Info */}
      <div className="grid grid-cols-2 gap-3">
        <Card className="p-4 rounded-xl">
          <div className="flex items-center gap-2">
            <Mail className="h-4 w-4 text-muted-foreground" />
            <div>
              <p className="text-xs text-muted-foreground">Email</p>
              <p className="text-sm font-medium">{tenant.email}</p>
            </div>
          </div>
        </Card>
        <Card className="p-4 rounded-xl">
          <div className="flex items-center gap-2">
            <Badge
              variant="secondary"
              className={`text-[10px] rounded-md ${
                !tenant.isActive ? 'bg-muted text-muted-foreground' :
                tenant.paymentStatus === 'current' ? 'bg-accent/15 text-accent' :
                tenant.paymentStatus === 'late' ? 'bg-warning/15 text-warning-foreground' :
                'bg-destructive/15 text-destructive'
              }`}
            >
              {!tenant.isActive ? 'past tenant' : tenant.paymentStatus}
            </Badge>
            <span className="text-sm font-medium">${tenant.rentAmount.toLocaleString()}/mo</span>
          </div>
        </Card>
      </div>

      {/* Portal link */}
      {tenant.portalUrl && <TenantPortalLink url={tenant.portalUrl} />}

      {/* Related Tasks */}
      {tenantTasks.length > 0 && (
        <div>
          <h2 className="text-sm font-bold mb-2">Related Tasks</h2>
          <div className="space-y-3">
            {tenantTasks.map(task => {
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
                </Card>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

export default TenantDetail;
