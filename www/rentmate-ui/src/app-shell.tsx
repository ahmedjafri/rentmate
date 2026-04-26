import { type ComponentType, type ReactNode, useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster as Sonner } from "./components/ui/sonner";
import { Toaster } from "./components/ui/toaster";
import { TooltipProvider } from "./components/ui/tooltip";
import { AppProvider } from "./context/AppContext";
import { AppLayout } from "./components/layout/AppLayout";
import { isAuthenticated } from "./lib/auth";
import Login from "./pages/Login";
import Index from "./pages/Index";
import Properties from "./pages/Properties";
import PropertyDetail from "./pages/PropertyDetail";
import Tenants from "./pages/Tenants";
import TenantDetail from "./pages/TenantDetail";
import ActionDesk from "./pages/ActionDesk";
import Tasks from "./pages/Tasks";
import TaskDetail from "./pages/TaskDetail";
import Chats from "./pages/Chats";
import Documents from "./pages/Documents";
import Vendors from "./pages/Vendors";
import DocumentPage from "./pages/DocumentPage";
import Settings from "./pages/Settings";
import DevTools from "./pages/DevTools";
import Routines from "./pages/Routines";
import RoutineDetail from "./pages/RoutineDetail";
import NotFound from "./pages/NotFound";
import PortalInvite from "./pages/VendorInvite";
import VendorPortal from "./pages/VendorPortal";
import TenantPortal from "./pages/TenantPortal";

const defaultQueryClient = new QueryClient();

type AnyComponent = ComponentType<any>;

type PublicRoute = {
  path: string;
  element: React.ReactNode;
};

export type AppComponentOverrides = Partial<{
  Login: typeof Login;
  Index: typeof Index;
  Properties: typeof Properties;
  PropertyDetail: typeof PropertyDetail;
  Tenants: typeof Tenants;
  TenantDetail: typeof TenantDetail;
  ActionDesk: typeof ActionDesk;
  Tasks: typeof Tasks;
  TaskDetail: typeof TaskDetail;
  Chats: typeof Chats;
  Documents: typeof Documents;
  DocumentPage: typeof DocumentPage;
  Vendors: typeof Vendors;
  Settings: typeof Settings;
  DevTools: typeof DevTools;
  Routines: typeof Routines;
  RoutineDetail: typeof RoutineDetail;
  NotFound: typeof NotFound;
  PortalInvite: typeof PortalInvite;
  VendorPortal: typeof VendorPortal;
  TenantPortal: typeof TenantPortal;
  AppLayout: typeof AppLayout;
}>;

function pickComponent<T extends AnyComponent>(override: AnyComponent | undefined, fallback: T): T {
  return (override ?? fallback) as T;
}

export function AuthGate({
  children,
  LoginComponent = Login,
}: {
  children: ReactNode;
  LoginComponent?: typeof Login;
}) {
  const [authed, setAuthed] = useState(isAuthenticated);

  useEffect(() => {
    const handleLogout = () => setAuthed(false);
    window.addEventListener("auth:logout", handleLogout);
    return () => window.removeEventListener("auth:logout", handleLogout);
  }, []);

  if (!authed) return <LoginComponent onSuccess={() => setAuthed(true)} />;
  return <>{children}</>;
}

export function BaseApp({
  componentOverrides,
  publicRoutes = [],
  queryClient = defaultQueryClient,
}: {
  componentOverrides?: AppComponentOverrides;
  publicRoutes?: PublicRoute[];
  queryClient?: QueryClient;
}) {
  const LoginComponent = pickComponent(componentOverrides?.Login, Login);
  const IndexComponent = pickComponent(componentOverrides?.Index, Index);
  const PropertiesComponent = pickComponent(componentOverrides?.Properties, Properties);
  const PropertyDetailComponent = pickComponent(componentOverrides?.PropertyDetail, PropertyDetail);
  const TenantsComponent = pickComponent(componentOverrides?.Tenants, Tenants);
  const TenantDetailComponent = pickComponent(componentOverrides?.TenantDetail, TenantDetail);
  const ActionDeskComponent = pickComponent(componentOverrides?.ActionDesk, ActionDesk);
  const TasksComponent = pickComponent(componentOverrides?.Tasks, Tasks);
  const TaskDetailComponent = pickComponent(componentOverrides?.TaskDetail, TaskDetail);
  const ChatsComponent = pickComponent(componentOverrides?.Chats, Chats);
  const DocumentsComponent = pickComponent(componentOverrides?.Documents, Documents);
  const DocumentPageComponent = pickComponent(componentOverrides?.DocumentPage, DocumentPage);
  const VendorsComponent = pickComponent(componentOverrides?.Vendors, Vendors);
  const SettingsComponent = pickComponent(componentOverrides?.Settings, Settings);
  const DevToolsComponent = pickComponent(componentOverrides?.DevTools, DevTools);
  const RoutinesComponent = pickComponent(componentOverrides?.Routines, Routines);
  const RoutineDetailComponent = pickComponent(
    componentOverrides?.RoutineDetail,
    RoutineDetail,
  );
  const NotFoundComponent = pickComponent(componentOverrides?.NotFound, NotFound);
  const PortalInviteComponent = pickComponent(componentOverrides?.PortalInvite, PortalInvite);
  const VendorPortalComponent = pickComponent(componentOverrides?.VendorPortal, VendorPortal);
  const TenantPortalComponent = pickComponent(componentOverrides?.TenantPortal, TenantPortal);
  const LayoutComponent = pickComponent(componentOverrides?.AppLayout, AppLayout);

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <Toaster />
        <Sonner />
        <BrowserRouter>
          <Routes>
            {publicRoutes.map((route) => (
              <Route key={route.path} path={route.path} element={route.element} />
            ))}
            <Route path="/t/:token" element={<PortalInviteComponent />} />
            <Route path="/vendor-portal" element={<VendorPortalComponent />} />
            <Route path="/tenant-portal" element={<TenantPortalComponent />} />
            <Route
              path="*"
              element={
                <AuthGate LoginComponent={LoginComponent}>
                  <AppProvider>
                    <LayoutComponent>
                      <Routes>
                        <Route path="/" element={<IndexComponent />} />
                        <Route path="/properties" element={<PropertiesComponent />} />
                        <Route path="/properties/:id" element={<PropertyDetailComponent />} />
                        <Route path="/tenants" element={<TenantsComponent />} />
                        <Route path="/tenants/:id" element={<TenantDetailComponent />} />
                        <Route path="/vendors" element={<VendorsComponent />} />
                        <Route path="/documents" element={<DocumentsComponent />} />
                        <Route path="/documents/:id" element={<DocumentPageComponent />} />
                        <Route path="/action-desk" element={<ActionDeskComponent />} />
                        <Route path="/tasks" element={<TasksComponent />} />
                        <Route path="/tasks/:id" element={<TaskDetailComponent />} />
                        <Route path="/chats" element={<ChatsComponent />} />
                        <Route path="/routines" element={<RoutinesComponent />} />
                        <Route
                          path="/routines/:id"
                          element={<RoutineDetailComponent />}
                        />
                        <Route path="/settings" element={<SettingsComponent />} />
                        <Route path="/dev" element={<DevToolsComponent />} />
                        <Route path="*" element={<NotFoundComponent />} />
                      </Routes>
                    </LayoutComponent>
                  </AppProvider>
                </AuthGate>
              }
            />
          </Routes>
        </BrowserRouter>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
