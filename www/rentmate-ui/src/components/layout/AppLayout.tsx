import { Link } from 'react-router-dom';
import { Bell, ShieldCheck, Hand } from 'lucide-react';
import { SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar';
import { AppSidebar } from './Sidebar';
import { ChatPanel } from '@/components/chat/ChatPanel';
import { useApp } from '@/context/AppContext';
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover';
import { Badge } from '@/components/ui/badge';
import { categoryColors, categoryLabels } from '@/data/mockData';
import { cn } from '@/lib/utils';
import { formatDistanceToNow } from 'date-fns';

export function AppLayout({ children }: {children: React.ReactNode;}) {
  const { chatPanel, actionDeskTasks } = useApp();
  const attentionTasks = actionDeskTasks.filter(
    t => t.status === 'active' && (t.mode === 'waiting_approval' || t.mode === 'manual')
  );
  const attentionCount = attentionTasks.length;

  return (
    <SidebarProvider defaultOpen={false}>
        <div className="h-screen flex w-full overflow-hidden">
        <AppSidebar />
        <div className="flex-1 flex flex-col min-w-0 h-full">
          <header className="h-10 flex items-center border-b px-4 bg-card/50 backdrop-blur-sm shrink-0">
            <SidebarTrigger className="mr-3" />
            <h2 className="text-xs font-medium text-muted-foreground flex-1">
</h2>
            {attentionCount > 0 && (
              <Popover>
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
                    <Link to="/action-desk" className="text-xs text-muted-foreground hover:text-foreground transition-colors">
                      View all →
                    </Link>
                  </div>
                  <ul className="divide-y max-h-96 overflow-y-auto">
                    {attentionTasks.map(task => (
                      <li key={task.id}>
                        <Link
                          to="/action-desk"
                          className="flex flex-col gap-1 px-3 py-2.5 hover:bg-muted/50 transition-colors"
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
                        </Link>
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