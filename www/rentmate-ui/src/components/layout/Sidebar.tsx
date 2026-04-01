import { Home, Building2, Users, ClipboardList, Lightbulb, Settings, Bot, FileText, Zap, MessageCircle, Wrench } from 'lucide-react';
import { NavLink } from '@/components/NavLink';
import { useLocation } from 'react-router-dom';
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
  useSidebar,
} from '@/components/ui/sidebar';

const navItems = [
  { title: 'Dashboard', url: '/', icon: Home },
  { title: 'Properties', url: '/properties', icon: Building2 },
  { title: 'Tenants', url: '/tenants', icon: Users },
  { title: 'Vendors', url: '/vendors', icon: Wrench },
  { title: 'Documents', url: '/documents', icon: FileText },
  { title: 'Suggestions', url: '/action-desk', icon: Lightbulb },
  { title: 'Tasks', url: '/tasks', icon: ClipboardList },
  { title: 'Chats', url: '/chats', icon: MessageCircle },
  { title: 'Automation', url: '/automation', icon: Zap },
  { title: 'Settings', url: '/settings', icon: Settings },
];

export function AppSidebar() {
  const { state, setOpen, isMobile, setOpenMobile } = useSidebar();
  const dismissSidebar = () => isMobile ? setOpenMobile(false) : setOpen(false);
  const location = useLocation();

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
    </SidebarUI>
  );
}
