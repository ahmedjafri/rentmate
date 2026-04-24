import { useEffect, useState } from 'react';
import { Home, Building2, Users, ClipboardList, Lightbulb, Settings, Bot, FileText, Zap, MessageCircle, Wrench, LogOut } from 'lucide-react';
import { NavLink } from '@/components/NavLink';
import { useLocation } from 'react-router-dom';
import { logout } from '@/lib/auth';
import { getMe } from '@/graphql/client';
import {
  Sidebar as SidebarUI,
  SidebarContent,
  SidebarFooter,
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
  { title: 'Routines', url: '/routines', icon: Zap },
  { title: 'Settings', url: '/settings', icon: Settings },
];

export function AppSidebar() {
  const { state, setOpen, isMobile, setOpenMobile } = useSidebar();
  const dismissSidebar = () => isMobile ? setOpenMobile(false) : setOpen(false);
  const location = useLocation();

  const [email, setEmail] = useState('');

  useEffect(() => {
    getMe()
      .then(({ me }) => setEmail(me.username))
      .catch(() => {});
  }, []);

  const initial = (email || 'U')[0].toUpperCase();

  const handleLogout = () => {
    logout();
    window.dispatchEvent(new Event('auth:logout'));
  };

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

      <SidebarFooter className="border-t">
        <div className="flex items-center gap-2 overflow-hidden">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-medium">
            {initial}
          </div>
          <div className="overflow-hidden transition-all duration-200 ease-linear group-data-[collapsible=icon]:w-0 group-data-[collapsible=icon]:opacity-0 w-auto opacity-100 flex-1 min-w-0">
            <p className="text-sm font-medium truncate">{email}</p>
          </div>
          <button
            onClick={handleLogout}
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
