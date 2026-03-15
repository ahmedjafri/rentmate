import { Home, Building2, Users, ClipboardList, Settings, MessageCircle, Bot, FileText, Zap } from 'lucide-react';
import { NavLink } from '@/components/NavLink';
import { useLocation } from 'react-router-dom';
import { useApp } from '@/context/AppContext';
import {
  Sidebar as SidebarUI,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarHeader,
  SidebarFooter,
  useSidebar,
} from '@/components/ui/sidebar';
import { Button } from '@/components/ui/button';

const navItems = [
  { title: 'Dashboard', url: '/', icon: Home },
  { title: 'Properties', url: '/properties', icon: Building2 },
  { title: 'Tenants', url: '/tenants', icon: Users },
  { title: 'Documents', url: '/documents', icon: FileText },
  { title: 'Action Desk', url: '/action-desk', icon: ClipboardList },
  { title: 'Automation', url: '/automation', icon: Zap },
  { title: 'Settings', url: '/settings', icon: Settings },
];

export function AppSidebar() {
  const { state, setOpen, isMobile, setOpenMobile } = useSidebar();
  const dismissSidebar = () => isMobile ? setOpenMobile(false) : setOpen(false);
  const collapsed = state === 'collapsed';
  const location = useLocation();
  const { openChat } = useApp();

  return (
    <SidebarUI
      collapsible="icon"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <SidebarHeader className="p-2 group-data-[state=expanded]:p-4 transition-all duration-200">
        <div className="flex items-center gap-2 overflow-hidden">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-primary">
            <Bot className="h-4 w-4 text-primary-foreground" />
          </div>
          <div className="overflow-hidden transition-all duration-200 ease-linear group-data-[collapsible=icon]:w-0 group-data-[collapsible=icon]:opacity-0 w-auto opacity-100">
            <h1 className="text-lg font-bold whitespace-nowrap">RentMate</h1>
            <p className="text-xs text-muted-foreground whitespace-nowrap">AI Property Manager</p>
          </div>
        </div>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Navigation</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {navItems.map((item) => (
                <SidebarMenuItem key={item.title}>
                  <SidebarMenuButton asChild>
                    <NavLink to={item.url} end={item.url === '/'} className="hover:bg-muted/50" activeClassName="bg-sidebar-accent text-sidebar-accent-foreground font-medium" onClick={dismissSidebar}>
                      <item.icon className="mr-2 h-4 w-4 shrink-0" />
                      <span className="truncate">{item.title}</span>
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="p-2 group-data-[state=expanded]:p-3 transition-all duration-200">
        <Button
          onClick={() => openChat()}
          variant="outline"
          size="icon"
          className={`rounded-xl border-primary/20 hover:bg-primary/5 overflow-hidden transition-all duration-200 shrink-0 ${
            collapsed ? 'h-8 w-8' : 'h-9 w-full justify-start gap-2 px-3'
          }`}
        >
          <MessageCircle className="h-4 w-4 text-primary shrink-0" />
          {!collapsed && <span className="text-sm truncate">Chat with AI</span>}
        </Button>
      </SidebarFooter>
    </SidebarUI>
  );
}
