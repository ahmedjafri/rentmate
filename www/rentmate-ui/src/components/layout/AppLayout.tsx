import { Link } from 'react-router-dom';
import { Bell } from 'lucide-react';
import { SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar';
import { AppSidebar } from './Sidebar';
import { ChatPanel } from '@/components/chat/ChatPanel';
import { useApp } from '@/context/AppContext';

export function AppLayout({ children }: {children: React.ReactNode;}) {
  const { chatPanel, actionDeskTasks } = useApp();
  const attentionCount = actionDeskTasks.filter(
    t => t.status === 'active' && (t.mode === 'waiting_approval' || t.mode === 'manual')
  ).length;

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
              <Link
                to="/action-desk"
                className="relative flex items-center justify-center h-7 w-7 rounded-md hover:bg-muted transition-colors"
                title={`${attentionCount} task${attentionCount === 1 ? '' : 's'} need attention`}
              >
                <Bell className="h-4 w-4 text-muted-foreground" />
                <span className="absolute -top-0.5 -right-0.5 h-4 min-w-4 px-1 flex items-center justify-center rounded-full bg-destructive text-destructive-foreground text-[10px] font-bold leading-none">
                  {attentionCount > 99 ? '99+' : attentionCount}
                </span>
              </Link>
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