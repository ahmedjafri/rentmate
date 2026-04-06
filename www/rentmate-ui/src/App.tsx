import { useState, useEffect } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppProvider } from "@/context/AppContext";
import { AppLayout } from "@/components/layout/AppLayout";
import { isAuthenticated } from "@/lib/auth";
import Login from "./pages/Login";
import Index from "./pages/Index";
import Properties from "./pages/Properties";
import PropertyDetail from "./pages/PropertyDetail";
import Tenants from "./pages/Tenants";
import TenantDetail from "./pages/TenantDetail";
import ActionDesk from "./pages/ActionDesk";
import Tasks from "./pages/Tasks";
import Chats from "./pages/Chats";
import Documents from "./pages/Documents";
import Vendors from "./pages/Vendors";
import DocumentPage from './pages/DocumentPage';
import Settings from "./pages/Settings";
import DevTools from "./pages/DevTools";
import Automation from "./pages/Automation";
import AutomationDetail from "./pages/AutomationDetail";
import NotFound from "./pages/NotFound";
import PortalInvite from "./pages/VendorInvite";
import VendorPortal from "./pages/VendorPortal";
import TenantPortal from "./pages/TenantPortal";

const queryClient = new QueryClient();

const AuthGate = ({ children }: { children: React.ReactNode }) => {
  const [authed, setAuthed] = useState(isAuthenticated);

  useEffect(() => {
    const handleLogout = () => setAuthed(false);
    window.addEventListener('auth:logout', handleLogout);
    return () => window.removeEventListener('auth:logout', handleLogout);
  }, []);

  if (!authed) return <Login onSuccess={() => setAuthed(true)} />;
  return <>{children}</>;
};

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <Routes>
          <Route path="/t/:token" element={<PortalInvite />} />
          <Route path="/vendor-portal" element={<VendorPortal />} />
          <Route path="/tenant-portal" element={<TenantPortal />} />
          <Route path="*" element={
            <AuthGate>
              <AppProvider>
                <AppLayout>
                  <Routes>
                    <Route path="/" element={<Index />} />
                    <Route path="/properties" element={<Properties />} />
                    <Route path="/properties/:id" element={<PropertyDetail />} />
                    <Route path="/tenants" element={<Tenants />} />
                    <Route path="/tenants/:id" element={<TenantDetail />} />
                    <Route path="/vendors" element={<Vendors />} />
                    <Route path="/documents" element={<Documents />} />
                    <Route path="/documents/:id" element={<DocumentPage />} />
                    <Route path="/action-desk" element={<ActionDesk />} />
                    <Route path="/tasks" element={<Tasks />} />
                    <Route path="/chats" element={<Chats />} />
                    <Route path="/automation" element={<Automation />} />
                    <Route path="/automation/:key" element={<AutomationDetail />} />
                    <Route path="/settings" element={<Settings />} />
                    <Route path="/dev" element={<DevTools />} />
                    <Route path="*" element={<NotFound />} />
                  </Routes>
                </AppLayout>
              </AppProvider>
            </AuthGate>
          } />
        </Routes>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
