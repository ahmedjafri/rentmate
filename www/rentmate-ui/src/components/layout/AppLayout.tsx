import { SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar';
import { AppSidebar } from './Sidebar';
import { ChatPanel } from '@/components/chat/ChatPanel';
import { useApp } from '@/context/AppContext';

export function AppLayout({ children }: {children: React.ReactNode;}) {
  const { chatPanel } = useApp();

  return (
    <SidebarProvider defaultOpen={false}>
        <div className="h-screen flex w-full overflow-hidden">
        <AppSidebar />
        <div className="flex-1 flex flex-col min-w-0 h-full">
          <header className="h-10 flex items-center border-b px-4 bg-card/50 backdrop-blur-sm shrink-0">
            <SidebarTrigger className="mr-3" />
            <h2 className="text-xs font-medium text-muted-foreground">
</h2>
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