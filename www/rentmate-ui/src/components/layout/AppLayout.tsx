import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Bell, ShieldCheck, Hand, Bot } from 'lucide-react';
import { SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar';
import { AppSidebar } from './Sidebar';
import { ChatPanel } from '@/components/chat/ChatPanel';
import { useApp } from '@/context/AppContext';
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { categoryColors, categoryLabels } from '@/data/mockData';
import { cn } from '@/lib/utils';
import { formatDistanceToNow } from 'date-fns';

function usePageContext() {
  const location = useLocation();
  const { properties, tenants, actionDeskTasks } = useApp();
  const path = location.pathname;

  const propMatch = path.match(/^\/properties\/([^/]+)$/);
  if (propMatch) {
    const p = properties.find(x => x.id === propMatch[1]);
    if (p) {
      const tasks = actionDeskTasks.filter(t => t.propertyId === p.id && t.status === 'active');
      const occupants = tenants.filter(t => t.propertyId === p.id && t.isActive);
      const label = p.name || p.address;
      return {
        label,
        contextKey: `property:${p.id}`,
        sessionTitle: `Ask about ${label}`,
        context: [
          `Property: ${p.name || p.address}`,
          `Address: ${p.address}`,
          `Units: ${p.units} total, ${p.occupiedUnits} occupied`,
          `Monthly revenue: $${p.monthlyRevenue.toLocaleString()}`,
          occupants.length ? `Active tenants: ${occupants.map(t => `${t.name} (${t.unit})`).join(', ')}` : 'No active tenants',
          tasks.length ? `Open tasks: ${tasks.map(t => t.title).join('; ')}` : 'No open tasks',
        ].join('\n'),
      };
    }
  }

  const tenantMatch = path.match(/^\/tenants\/([^/]+)$/);
  if (tenantMatch) {
    const t = tenants.find(x => x.id === tenantMatch[1]);
    if (t) {
      const p = properties.find(x => x.id === t.propertyId);
      const tasks = actionDeskTasks.filter(x => x.status === 'active' && x.participants.some(pp => pp.name === t.name));
      return {
        label: t.name,
        contextKey: `tenant:${t.id}`,
        sessionTitle: `Ask about ${t.name}`,
        context: [
          `Tenant: ${t.name}`,
          p ? `Property: ${p.name || p.address}, Unit ${t.unit}` : `Unit: ${t.unit}`,
          `Rent: $${t.rentAmount.toLocaleString()}/mo (${t.paymentStatus})`,
          `Lease ends: ${t.leaseEnd instanceof Date ? t.leaseEnd.toLocaleDateString() : new Date(t.leaseEnd as unknown as string).toLocaleDateString()}`,
          tasks.length ? `Related tasks: ${tasks.map(x => x.title).join('; ')}` : '',
        ].filter(Boolean).join('\n'),
      };
    }
  }

  if (path === '/action-desk') {
    const needs = actionDeskTasks.filter(t => t.status === 'active' && (t.mode === 'waiting_approval' || t.mode === 'manual'));
    return {
      label: 'Action Desk',
      contextKey: 'page:action-desk',
      sessionTitle: 'Ask about Action Desk',
      context: needs.length
        ? `Action Desk — ${needs.length} task${needs.length !== 1 ? 's' : ''} need attention:\n${needs.map(t => `• ${t.title} (${t.mode === 'waiting_approval' ? 'needs approval' : 'manual'})`).join('\n')}`
        : 'Action Desk — all tasks are running autonomously.',
    };
  }

  if (path === '/properties') {
    return { label: 'Properties', contextKey: 'page:properties', sessionTitle: 'Ask about Properties', context: `Viewing all ${properties.length} properties.` };
  }

  if (path === '/tenants') {
    const active = tenants.filter(t => t.isActive).length;
    return { label: 'Tenants', contextKey: 'page:tenants', sessionTitle: 'Ask about Tenants', context: `Viewing ${active} active tenants out of ${tenants.length} total.` };
  }

  return null;
}

export function AppLayout({ children }: {children: React.ReactNode;}) {
  const { chatPanel, openChat, closeChat, actionDeskTasks, chatSessions } = useApp();
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const pageCtx = usePageContext();

  const attentionTasks = actionDeskTasks.filter(
    t => t.status === 'active' && (t.mode === 'waiting_approval' || t.mode === 'manual')
  );
  const attentionCount = attentionTasks.length;

  const close = () => setOpen(false);

  const handleRentMateClick = () => {
    const targetContextKey = pageCtx?.contextKey ?? null;
    if (chatPanel.isOpen && !chatPanel.taskId) {
      // Only toggle closed when the current session is already showing this context.
      // If the user navigated to a different page, switch context instead of closing.
      const currentSession = chatSessions.find(s => s.id === chatPanel.sessionId);
      if ((currentSession?.contextKey ?? null) === targetContextKey) {
        closeChat();
        return;
      }
    }
    openChat({
      pageContext: pageCtx?.context ?? null,
      contextKey: targetContextKey,
      sessionTitle: pageCtx?.sessionTitle ?? null,
    });
  };

  return (
    <SidebarProvider defaultOpen={false}>
        <div className="h-screen flex w-full overflow-hidden">
        <AppSidebar />
        <div className="flex-1 flex flex-col min-w-0 h-full">
          <header className="h-10 flex items-center border-b px-4 bg-card/50 backdrop-blur-sm shrink-0 gap-2">
            <SidebarTrigger className="mr-1 shrink-0" />

            {/* RentMate chat button */}
            <Button
              variant={chatPanel.isOpen && !chatPanel.taskId ? 'default' : 'outline'}
              size="sm"
              onClick={handleRentMateClick}
              className="h-7 rounded-xl gap-1.5 text-xs shrink-0"
            >
              <Bot className="h-3.5 w-3.5 shrink-0" />
              <span className="hidden sm:inline">
                {pageCtx ? `Ask about ${pageCtx.label}` : 'Ask RentMate'}
              </span>
            </Button>

            <div className="flex-1" />

            {attentionCount > 0 && (
              <Popover open={open} onOpenChange={setOpen}>
                <PopoverTrigger asChild>
                  <button
                    className="relative flex items-center justify-center h-7 w-7 rounded-md hover:bg-muted transition-colors"
                    aria-label={`${attentionCount} task${attentionCount === 1 ? '' : 's'} need attention`}
                  >
                    <Bell className="h-4 w-4 text-muted-foreground" />
                    <span className="absolute -top-0.5 -right-0.5 h-4 min-w-4 px-1 flex items-center justify-center rounded-full bg-destructive text-destructive-foreground text-[10px] font-bold leading-none">
                      {attentionCount > 99 ? '99+' : attentionCount}
                    </span>
                  </button>
                </PopoverTrigger>
                <PopoverContent align="end" sideOffset={8} className="w-80 p-0">
                  <div className="flex items-center justify-between px-3 py-2 border-b">
                    <span className="text-sm font-semibold">Needs Attention</span>
                    <button
                      onClick={() => { close(); navigate('/action-desk'); }}
                      className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                    >
                      View all →
                    </button>
                  </div>
                  <ul className="divide-y max-h-96 overflow-y-auto">
                    {attentionTasks.map(task => (
                      <li key={task.id}>
                        <button
                          onClick={() => { close(); navigate(`/action-desk?task=${task.id}`); }}
                          className="w-full text-left flex flex-col gap-1 px-3 py-2.5 hover:bg-muted/50 transition-colors"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <span className="text-sm font-medium leading-tight line-clamp-1">{task.title}</span>
                            {task.mode === 'waiting_approval'
                              ? <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-amber-500 mt-0.5" />
                              : <Hand className="h-3.5 w-3.5 shrink-0 text-muted-foreground mt-0.5" />
                            }
                          </div>
                          <div className="flex items-center gap-1.5">
                            <Badge variant="secondary" className={cn('text-[10px] py-0 px-1.5 h-4 rounded', categoryColors[task.category])}>
                              {categoryLabels[task.category]}
                            </Badge>
                            <span className="text-[10px] text-muted-foreground">
                              {formatDistanceToNow(task.lastMessageAt, { addSuffix: true })}
                            </span>
                          </div>
                        </button>
                      </li>
                    ))}
                  </ul>
                </PopoverContent>
              </Popover>
            )}
          </header>
          <div className="flex flex-1 min-h-0">
            <main className="flex-1 overflow-auto">
              {children}
            </main>
            {chatPanel.isOpen && <ChatPanel />}
          </div>
        </div>
      </div>
    </SidebarProvider>);
}
