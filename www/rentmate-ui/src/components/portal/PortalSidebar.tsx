import { Home, LogOut } from 'lucide-react';

import { NavLink } from '@/components/NavLink';
import {
  Sidebar as SidebarUI,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar';

export function PortalSidebar({
  title,
  subtitle,
  dashboardPath,
  identity,
  onLogout,
}: {
  title: string;
  subtitle: string;
  dashboardPath: string;
  identity: string;
  onLogout: () => void;
}) {
  const { setOpen, isMobile, setOpenMobile } = useSidebar();
  const dismissSidebar = () => (isMobile ? setOpenMobile(false) : setOpen(false));
  const initial = (identity || 'U')[0]?.toUpperCase() ?? 'U';

  return (
    <SidebarUI collapsible="icon" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <SidebarHeader className="p-2 group-data-[state=expanded]:p-4 transition-all duration-200">
        <div className="flex items-center gap-2 overflow-hidden">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-primary">
            <Home className="h-4 w-4 text-primary-foreground" />
          </div>
          <div className="overflow-hidden transition-all duration-200 ease-linear group-data-[collapsible=icon]:w-0 group-data-[collapsible=icon]:opacity-0 w-auto opacity-100">
            <h1 className="text-lg font-bold whitespace-nowrap">{title}</h1>
            <p className="text-xs text-muted-foreground whitespace-nowrap">{subtitle}</p>
          </div>
        </div>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Navigation</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild>
                  <NavLink
                    to={dashboardPath}
                    end
                    className="hover:bg-muted/50"
                    activeClassName="bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                    onClick={dismissSidebar}
                  >
                    <Home className="mr-2 h-4 w-4 shrink-0" />
                    <span className="truncate">Dashboard</span>
                  </NavLink>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="border-t">
        <div className="flex items-center gap-2 overflow-hidden">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-medium">
            {initial}
          </div>
          <div className="overflow-hidden transition-all duration-200 ease-linear group-data-[collapsible=icon]:w-0 group-data-[collapsible=icon]:opacity-0 w-auto opacity-100 flex-1 min-w-0">
            <p className="text-sm font-medium truncate">{identity}</p>
          </div>
          <button
            onClick={onLogout}
            className="shrink-0 p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors group-data-[collapsible=icon]:hidden"
            title="Sign out"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </SidebarFooter>
    </SidebarUI>
  );
}
