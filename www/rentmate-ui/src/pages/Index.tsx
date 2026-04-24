import { useEffect, useMemo, useRef, useState } from 'react';
import { useApp } from '@/context/AppContext';
import { PageLoader } from '@/components/ui/page-loader';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Link, useNavigate } from 'react-router-dom';
import { Building2, Users, Wrench, ShieldCheck, Bot, Clock, MessageCircle, Hand, Lock, Zap, Plus } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { TaskMode, TaskParticipantType, categoryColors, categoryLabels } from '@/data/mockData';
import { cn } from '@/lib/utils';
import { ChatPanel } from '@/components/chat/ChatPanel';
import { ConvRow } from '@/components/chat/ConvRow';
import { useConversations } from '@/hooks/useConversations';
import { actOnSuggestion, deleteConversation } from '@/graphql/client';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { SuggestionCard } from './ActionDesk';

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
  const navigate = useNavigate();
  const { properties, tenants, vendors, actionDeskTasks, suggestions, updateSuggestionStatus, refreshData, openChat, closeChat, chatPanel, isLoading } = useApp();
  const { conversations: aiConvs, loading: aiLoading, refresh: refreshAi, removeConversation: removeAiConv } = useConversations('user_ai', 20);
  const { conversations: tenantConvs, loading: tenantLoading, refresh: refreshTenants, removeConversation: removeTenantConv } = useConversations('tenant', 20);
  const { conversations: vendorConvs, loading: vendorLoading, refresh: refreshVendors, removeConversation: removeVendorConv } = useConversations('vendor', 20);

  const convsLoading = aiLoading || tenantLoading || vendorLoading;
  const allConversations = useMemo(() =>
    [...aiConvs, ...tenantConvs, ...vendorConvs].sort((a, b) => {
      const aTime = a.lastMessageAt ?? a.updatedAt;
      const bTime = b.lastMessageAt ?? b.updatedAt;
      return new Date(bTime).getTime() - new Date(aTime).getTime();
    }).slice(0, 30),
    [aiConvs, tenantConvs, vendorConvs]
  );
  const refreshAllConvs = () => { refreshAi(); refreshTenants(); refreshVendors(); };
  const removeConversation = (uid: string) => { removeAiConv(uid); removeTenantConv(uid); removeVendorConv(uid); };
  const [showNewChat, setShowNewChat] = useState(false);

  const totalUnits = properties.reduce((a, p) => a + p.units, 0);
  const activeTenants = tenants.filter(t => t.isActive);

  const needsAttention = actionDeskTasks.filter(
    t => t.status === 'active' && (t.mode === 'waiting_approval' || t.mode === 'manual')
  );
  const pendingSuggestions = suggestions.filter(s => s.status === 'pending');

  // Track latest chatPanel in a ref so the cleanup can read it without stale closure
  const chatPanelRef = useRef(chatPanel);
  chatPanelRef.current = chatPanel;

  // Clear "New Chat" indicator once a backend conversation is created
  useEffect(() => {
    if (chatPanel.conversationId) setShowNewChat(false);
  }, [chatPanel.conversationId]);

  // Open the chat panel on dashboard mount (no conversation created yet)
  // On unmount, close unsaved lazy chats (no backend conversation)
  useEffect(() => {
    if (!chatPanel.isOpen) {
      openChat({ lazy: true });
    }
    return () => {
      const cp = chatPanelRef.current;
      if (cp.isOpen && !cp.conversationId && !cp.taskId && !cp.suggestionId) {
        closeChat();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (isLoading) return <PageLoader />;

  const stats = [
    { label: 'Properties', value: properties.length, icon: Building2, sub: `${totalUnits} units`, link: '/properties' },
    { label: 'Tenants', value: tenants.length, icon: Users, sub: `${activeTenants.length} active`, link: '/tenants' },
    { label: 'Vendors', value: vendors.length, icon: Wrench, sub: `contractors`, link: '/vendors' },
  ];

  const handleSuggestionAction = async (suggestionId: string, action: string, editedBody?: string) => {
    const suggestion = suggestions.find(item => item.id === suggestionId);
    if (action === 'request_file_upload') {
      if (suggestion?.taskId) {
        openChat({ taskId: suggestion.taskId });
        toast.info('Open the task chat and upload the requested file there.');
      } else {
        toast.info('Open the linked suggestion and upload the requested file from the task chat.');
      }
      return;
    }
    try {
      const result = await actOnSuggestion(suggestionId, action, editedBody ?? null);
      const { status, taskId } = result.actOnSuggestion;
      updateSuggestionStatus(suggestionId, status.toLowerCase() as 'accepted' | 'dismissed');
      // Re-fetch so new tasks appear immediately
      refreshData();
      if (status === 'accepted') {
        toast.success(taskId ? 'Task created' : 'Suggestion accepted');
      } else {
        toast.info('Suggestion dismissed');
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Action failed');
    }
  };

  return (
    <div className="flex flex-col md:flex-row h-full">
      {/* Left column: Conversation list */}
      <div className="w-72 min-w-[280px] shrink-0 border-r hidden md:flex flex-col h-full">
        <div className="flex items-center justify-between px-4 py-3 border-b shrink-0">
          <h2 className="text-sm font-semibold">Chats</h2>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            title="New chat"
            onClick={() => { openChat({ lazy: true }); setShowNewChat(true); }}
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex-1 overflow-auto p-2 space-y-1.5">
          {/* Show "New Chat" row when user explicitly clicked "+" */}
          {showNewChat && !chatPanel.conversationId && !chatPanel.taskId && !chatPanel.suggestionId && (
            <Card className="px-3 py-2.5 rounded-xl ring-2 ring-primary/40 bg-primary/5">
              <div className="flex items-center gap-1.5">
                <Badge variant="secondary" className="text-[10px] rounded-lg gap-1 shrink-0 bg-primary/10 text-primary">
                  <Bot className="h-3 w-3" />
                  RentMate
                </Badge>
              </div>
              <h3 className="font-medium text-sm mt-1.5">New Chat</h3>
            </Card>
          )}
          {convsLoading && <p className="text-xs text-muted-foreground text-center py-4">Loading…</p>}
          {!convsLoading && allConversations.length === 0 && chatPanel.conversationId && (
            <p className="text-xs text-muted-foreground text-center py-4">No conversations yet</p>
          )}
          {allConversations.map(conv => (
            <ConvRow
              key={conv.uid}
              conv={conv}
              isActive={chatPanel.conversationId === conv.uid}
              onClick={() => { setShowNewChat(false); openChat({ conversationId: conv.uid }); }}
              onDelete={async () => {
                try {
                  await deleteConversation(conv.uid);
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

      {/* Middle column: Chat */}
      <div className="flex-[2] min-w-0 flex flex-col h-full">
        <ChatPanel embedded />
      </div>

      {/* Right column: Stats + Action Desk */}
      <div className="w-96 min-w-[360px] shrink-0 overflow-auto hidden lg:block border-l">
        <div className="p-4 space-y-4">
          {/* Welcome */}
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10">
              <Bot className="h-4 w-4 text-primary" />
            </div>
            <div>
              <h1 className="text-lg font-bold">{new Date().getHours() < 12 ? 'Good morning!' : new Date().getHours() < 17 ? 'Good afternoon!' : 'Good evening!'}</h1>
              <p className="text-xs text-muted-foreground">
                <span className="font-medium text-foreground">{needsAttention.length + pendingSuggestions.length} items</span> need attention
              </p>
            </div>
          </div>

          {/* Stats */}
          <div className="grid grid-cols-3 gap-2">
            {stats.map((stat) => {
              const content = (
                <Card key={stat.label} className={cn('p-2.5 rounded-xl', stat.link && 'hover:shadow-md transition-shadow cursor-pointer')}>
                  <div className="flex items-center gap-1 mb-0.5">
                    <stat.icon className="h-3 w-3 text-muted-foreground" />
                    <span className="text-[9px] text-muted-foreground">{stat.label}</span>
                  </div>
                  <p className="text-base font-bold">{stat.value}</p>
                  <p className="text-[9px] text-muted-foreground">{stat.sub}</p>
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

            {needsAttention.length === 0 && pendingSuggestions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
                <ShieldCheck className="h-10 w-10 mb-2 opacity-40" />
                <p className="text-sm font-medium">Nothing needs attention</p>
                <p className="text-xs mt-1">All caught up!</p>
              </div>
            ) : (
              <div className="space-y-2">
                {/* Pending suggestions — full interactive cards */}
                {pendingSuggestions.map(sug => (
                  <SuggestionCard
                    key={sug.id}
                    suggestion={sug}
                    onAction={handleSuggestionAction}
                    compact
                  />
                ))}

                {/* Tasks needing attention */}
                {needsAttention.map(task => {
                  const mode = modeConfig[task.mode];
                  const ModeIcon = mode.icon;
                  const property = task.propertyId ? properties.find(p => p.id === task.propertyId) : null;

                  return (
                    <Card key={task.id} className="p-3 rounded-xl hover:shadow-md transition-shadow cursor-pointer" onClick={() => navigate(`/tasks/${task.id}`)}>
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
