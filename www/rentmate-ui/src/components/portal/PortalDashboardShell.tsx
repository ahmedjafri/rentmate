import type { ReactNode } from 'react';

import { SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar';

import { PortalSidebar } from './PortalSidebar';

export function PortalDashboardShell({
  title,
  subtitle,
  dashboardPath,
  identity,
  onLogout,
  children,
}: {
  title: string;
  subtitle: string;
  dashboardPath: string;
  identity: string;
  onLogout: () => void;
  children: ReactNode;
}) {
  return (
    <SidebarProvider>
      <PortalSidebar
        title={title}
        subtitle={subtitle}
        dashboardPath={dashboardPath}
        identity={identity}
        onLogout={onLogout}
      />
      <div className="flex min-h-screen flex-1 bg-muted/30">
        <div className="flex flex-1 flex-col overflow-hidden">
          <header className="bg-background border-b px-4 py-3 flex items-center shrink-0">
            <SidebarTrigger className="mr-2" />
            <h1 className="font-semibold text-sm">Dashboard</h1>
          </header>
          {children}
        </div>
      </div>
    </SidebarProvider>
  );
}
