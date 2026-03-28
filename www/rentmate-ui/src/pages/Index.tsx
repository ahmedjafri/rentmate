import { useApp } from '@/context/AppContext';
import { PageLoader } from '@/components/ui/page-loader';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Link } from 'react-router-dom';
import { Building2, Users, Wrench, TrendingUp, Bot, Clock, MessageCircle, ShieldCheck, Hand, Lock, Zap } from 'lucide-react';
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
  tenant: Users,
  vendor: Wrench,
  manager: Users,
};

const Index = () => {
  const { properties, tenants, tickets, actionDeskTasks, openChat, isLoading } = useApp();

  const totalUnits = properties.reduce((a, p) => a + p.units, 0);
  const occupiedUnits = properties.reduce((a, p) => a + p.occupiedUnits, 0);
  const occupancyRate = Math.round((occupiedUnits / totalUnits) * 100);
  const totalRevenue = properties.reduce((a, p) => a + p.monthlyRevenue, 0);
  const openTickets = tickets.filter(t => t.status === 'open' || t.status === 'in_progress').length;

  if (isLoading) return <PageLoader />;

  const needsAttention = actionDeskTasks.filter(
    t => t.status === 'active' && (t.mode === 'waiting_approval' || t.mode === 'manual')
  );

  const activeTenants = tenants.filter(t => t.isActive);

  const stats = [
    { label: 'Properties', value: properties.length, icon: Building2, sub: `${totalUnits} total units`, link: '/properties' },
    { label: 'Tenants', value: activeTenants.length, icon: Users, sub: `${totalUnits} total units` },
  ];

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      {/* Welcome */}
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
          <Bot className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Good morning! 👋</h1>
          <p className="text-sm text-muted-foreground">
            You have <span className="font-medium text-foreground">{needsAttention.length} tasks</span> that need your attention.
          </p>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {stats.map((stat) => {
          const content = (
            <Card key={stat.label} className={cn('p-4 rounded-xl', stat.link && 'hover:shadow-md transition-shadow cursor-pointer')}>
              <div className="flex items-center gap-2 mb-2">
                <stat.icon className="h-4 w-4 text-muted-foreground" />
                <span className="text-xs text-muted-foreground">{stat.label}</span>
              </div>
              <p className="text-xl font-bold">{stat.value}</p>
              <p className="text-[11px] text-muted-foreground mt-0.5">{stat.sub}</p>
            </Card>
          );
          return stat.link ? <Link key={stat.label} to={stat.link}>{content}</Link> : <div key={stat.label}>{content}</div>;
        })}
      </div>

      {/* Action Desk - Needs Attention */}
      <div>
        <div className="flex items-center gap-2 mb-4">
          <ShieldCheck className="h-5 w-5 text-primary" />
          <Link to="/action-desk" className="text-lg font-bold hover:underline">Action Desk</Link>
        </div>

        {needsAttention.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
            <ShieldCheck className="h-12 w-12 mb-3 opacity-40" />
            <p className="font-medium">No tasks need attention</p>
            <p className="text-sm mt-1">All caught up! 🎉</p>
          </div>
        ) : (
          <div className="space-y-3">
            {needsAttention.map(task => {
              const mode = modeConfig[task.mode];
              const ModeIcon = mode.icon;
              const property = task.propertyId ? properties.find(p => p.id === task.propertyId) : null;

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

                  <div className="mt-2.5 flex items-center justify-between">
                    <div className="flex items-center gap-1">
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
                    {property && (
                      <span className="text-[10px] text-muted-foreground">{property.name || property.address}</span>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default Index;
