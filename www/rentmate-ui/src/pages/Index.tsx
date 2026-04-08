import { useEffect } from 'react';
import { useApp } from '@/context/AppContext';
import { PageLoader } from '@/components/ui/page-loader';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Link } from 'react-router-dom';
import { Building2, Users, Wrench, ShieldCheck, Bot, Clock, MessageCircle, Hand, Lock, Zap } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { TaskMode, TaskParticipantType, categoryColors, categoryLabels } from '@/data/mockData';
import { cn } from '@/lib/utils';
import { ChatPanel } from '@/components/chat/ChatPanel';
import { ConvRow } from '@/components/chat/ConvRow';
import { useConversations } from '@/hooks/useConversations';
import { graphqlQuery, DELETE_CONVERSATION_MUTATION } from '@/data/api';
import { toast } from 'sonner';

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
  const { properties, tenants, actionDeskTasks, openChat, chatPanel, isLoading } = useApp();
  const { conversations, loading: convsLoading, refresh, removeConversation } = useConversations('user_ai', 8);

  const totalUnits = properties.reduce((a, p) => a + p.units, 0);
  const activeTenants = tenants.filter(t => t.isActive);

  const needsAttention = actionDeskTasks.filter(
    t => t.status === 'active' && (t.mode === 'waiting_approval' || t.mode === 'manual')
  );

  // Auto-open free chat on dashboard mount
  useEffect(() => {
    if (!chatPanel.isOpen) {
      openChat();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (isLoading) return <PageLoader />;

  const stats = [
    { label: 'Properties', value: properties.length, icon: Building2, sub: `${totalUnits} total units`, link: '/properties' },
    { label: 'Tenants', value: activeTenants.length, icon: Users, sub: `${totalUnits} total units` },
  ];

  return (
    <div className="flex flex-col md:flex-row h-full">
      {/* Left column: Chat + past chats (2/3) */}
      <div className="flex-[2] min-w-0 flex flex-col md:border-r h-full">
        <div className="flex-1 min-h-0">
          <ChatPanel embedded />
        </div>

        {/* Recent chats */}
        <div className="border-t p-4 space-y-2 overflow-auto max-h-[280px] shrink-0">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-muted-foreground">Recent Chats</h3>
            <Link to="/chats" className="text-xs text-muted-foreground hover:text-foreground transition-colors">
              View all →
            </Link>
          </div>
          {convsLoading && <p className="text-xs text-muted-foreground text-center py-2">Loading…</p>}
          {!convsLoading && conversations.length === 0 && (
            <p className="text-xs text-muted-foreground text-center py-2">No conversations yet</p>
          )}
          {conversations.map(conv => (
            <ConvRow
              key={conv.uid}
              conv={conv}
              isActive={chatPanel.conversationId === conv.uid}
              onClick={() => openChat({ conversationId: conv.uid })}
              onDelete={async () => {
                try {
                  await graphqlQuery(DELETE_CONVERSATION_MUTATION, { uid: conv.uid });
                  removeConversation(conv.uid);
                  toast.success('Conversation deleted');
                } catch {
                  toast.error('Failed to delete conversation');
                }
              }}
            />
          ))}
        </div>
      </div>

      {/* Right column: Stats + Action Desk (1/3) */}
      <div className="flex-1 min-w-0 overflow-auto hidden md:block">
        <div className="p-4 space-y-4">
          {/* Welcome */}
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10">
              <Bot className="h-4 w-4 text-primary" />
            </div>
            <div>
              <h1 className="text-lg font-bold">Good morning!</h1>
              <p className="text-xs text-muted-foreground">
                <span className="font-medium text-foreground">{needsAttention.length} tasks</span> need attention
              </p>
            </div>
          </div>

          {/* Stats */}
          <div className="grid grid-cols-2 gap-2">
            {stats.map((stat) => {
              const content = (
                <Card key={stat.label} className={cn('p-3 rounded-xl', stat.link && 'hover:shadow-md transition-shadow cursor-pointer')}>
                  <div className="flex items-center gap-1.5 mb-1">
                    <stat.icon className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="text-[10px] text-muted-foreground">{stat.label}</span>
                  </div>
                  <p className="text-lg font-bold">{stat.value}</p>
                  <p className="text-[10px] text-muted-foreground">{stat.sub}</p>
                </Card>
              );
              return stat.link ? <Link key={stat.label} to={stat.link}>{content}</Link> : <div key={stat.label}>{content}</div>;
            })}
          </div>

          {/* Action Desk */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <ShieldCheck className="h-4 w-4 text-primary" />
              <Link to="/action-desk" className="text-sm font-bold hover:underline">Action Desk</Link>
            </div>

            {needsAttention.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
                <ShieldCheck className="h-10 w-10 mb-2 opacity-40" />
                <p className="text-sm font-medium">No tasks need attention</p>
                <p className="text-xs mt-1">All caught up!</p>
              </div>
            ) : (
              <div className="space-y-2">
                {needsAttention.map(task => {
                  const mode = modeConfig[task.mode];
                  const ModeIcon = mode.icon;
                  const property = task.propertyId ? properties.find(p => p.id === task.propertyId) : null;

                  return (
                    <Card key={task.id} className="p-3 rounded-xl hover:shadow-md transition-shadow cursor-pointer" onClick={() => openChat({ taskId: task.id })}>
                      <div className="flex items-start justify-between gap-2 mb-1.5">
                        <div className="flex items-center gap-1.5 flex-wrap">
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
                            </Badge>
                          )}
                        </div>
                        <div className="flex items-center gap-1 text-[10px] text-muted-foreground shrink-0">
                          <Clock className="h-3 w-3" />
                          {formatDistanceToNow(new Date(task.lastMessageAt), { addSuffix: true })}
                        </div>
                      </div>

                      <h3 className="font-semibold text-xs mb-1">{task.title}</h3>

                      <div className="flex items-start gap-2 mt-1.5 bg-muted/40 rounded-lg p-2">
                        <MessageCircle className="h-3 w-3 text-muted-foreground mt-0.5 shrink-0" />
                        <div className="min-w-0">
                          <span className="text-[10px] font-medium text-muted-foreground">{task.lastMessageBy}</span>
                          <p className="text-[11px] text-foreground line-clamp-2">{task.lastMessage}</p>
                        </div>
                      </div>

                      <div className="mt-2 flex items-center justify-between">
                        <div className="flex items-center gap-1">
                          {task.participants.map((p, i) => {
                            const Icon = participantIcon[p.type];
                            return (
                              <div
                                key={i}
                                className={cn(
                                  'flex h-4 w-4 items-center justify-center rounded-full text-[9px]',
                                  p.type === 'agent' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground'
                                )}
                                title={`${p.name} (${p.type})`}
                              >
                                <Icon className="h-2.5 w-2.5" />
                              </div>
                            );
                          })}
                          <span className="text-[9px] text-muted-foreground ml-1">
                            {task.participants.map(p => p.name.split(' ')[0]).join(', ')}
                          </span>
                        </div>
                        {property && (
                          <span className="text-[9px] text-muted-foreground">{property.name || property.address}</span>
                        )}
                      </div>
                    </Card>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default Index;
