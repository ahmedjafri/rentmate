// Entity context management for properties, tenants, and other entities
import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import {
  Suggestion, Property, Tenant, Vendor, MaintenanceTicket, AutonomySettings, ChatMessage, ActionDeskTask, ManagedDocument,
  defaultAutonomySettings,
  SuggestionStatus, ActionDeskTask as ADT,
} from '@/data/mockData';
import { useApiData } from '@/hooks/useApiData';
import { graphqlQuery, UPDATE_TASK_MUTATION } from '@/data/api';
import { getToken } from '@/lib/auth';
import { toast } from 'sonner';

/** UUID v4 that works in both secure and non-secure contexts (HTTP on LAN IPs). */
function generateSessionId(): string {
  if (typeof crypto?.randomUUID === 'function') return crypto.randomUUID();
  const buf = new Uint8Array(16);
  crypto.getRandomValues(buf);
  buf[6] = (buf[6] & 0x0f) | 0x40;
  buf[8] = (buf[8] & 0x3f) | 0x80;
  const hex = Array.from(buf, b => b.toString(16).padStart(2, '0'));
  return `${hex.slice(0,4).join('')}-${hex.slice(4,6).join('')}-${hex.slice(6,8).join('')}-${hex.slice(8,10).join('')}-${hex.slice(10).join('')}`;
}

interface ChatPanelState {
  isOpen: boolean;
  suggestionId: string | null;
  taskId: string | null;
  conversationId: string | null;
  pageContext: string | null;
}

interface AppContextType {
  properties: Property[];
  tenants: Tenant[];
  vendors: Vendor[];
  suggestions: Suggestion[];
  tickets: MaintenanceTicket[];
  actionDeskTasks: ActionDeskTask[];
  isLoading: boolean;
  documents: ManagedDocument[];
  autonomySettings: AutonomySettings;
  chatPanel: ChatPanelState;
  entityContext: Record<string, string>;
  getEntityContext: (entityId: string) => string;
  setEntityContext: (entityId: string, context: string) => void;
  updateSuggestionStatus: (id: string, status: SuggestionStatus) => void;
  updateSuggestion: (id: string, updates: Partial<Suggestion>) => void;
  addChatMessage: (context: { suggestionId?: string | null; taskId?: string | null }, message: ChatMessage) => void;
  updateTaskMessage: (taskId: string, messageId: string, updates: Partial<ChatMessage>) => void;
  setTaskMessages: (taskId: string, messages: ChatMessage[]) => void;
  updateTask: (taskId: string, updates: Partial<ActionDeskTask>) => void;
  removeTask: (taskId: string) => void;
  addTask: (task: ActionDeskTask) => void;
  addProperty: (prop: Property) => void;
  updateProperty: (id: string, updates: Partial<Property>) => void;
  removeProperty: (id: string) => void;
  addTenant: (tenant: Tenant) => void;
  updateTenant: (id: string, updates: Partial<Tenant>) => void;
  addVendor: (vendor: Vendor) => void;
  updateVendor: (id: string, updates: Partial<Vendor>) => void;
  removeVendor: (id: string) => void;
  addDocument: (doc: ManagedDocument) => void;
  updateDocument: (id: string, updates: Partial<ManagedDocument>) => void;
  replaceDocument: (oldId: string, doc: ManagedDocument) => void;
  removeDocument: (id: string) => void;
  openChat: (opts?: { suggestionId?: string | null; taskId?: string | null; pageContext?: string | null; conversationId?: string | null; lazy?: boolean }) => void;
  setChatConversationId: (id: string) => void;
  closeChat: () => void;
  setAutonomySettings: (settings: AutonomySettings) => void;
  refreshData: () => void;
}

const AppContext = createContext<AppContextType | undefined>(undefined);

// Helpers to coerce date fields that become strings after a localStorage round-trip.
function coerceDates<T extends { timestamp?: unknown }>(messages: T[]): T[] {
  return messages.map(m => ({ ...m, timestamp: m.timestamp instanceof Date ? m.timestamp : new Date(m.timestamp as string) }));
}

function coerceTask(t: ActionDeskTask): ActionDeskTask {
  return {
    ...t,
    lastMessageAt: t.lastMessageAt instanceof Date ? t.lastMessageAt : new Date(t.lastMessageAt as unknown as string),
    chatThread: coerceDates(t.chatThread),
  };
}

function coerceTenant(t: Tenant): Tenant {
  return { ...t, leaseEnd: t.leaseEnd instanceof Date ? t.leaseEnd : new Date(t.leaseEnd as unknown as string) };
}

const loadFromStorage = <T,>(key: string, fallback: T): T => {
  try {
    const stored = localStorage.getItem(key);
    if (stored) {
      const parsed = JSON.parse(stored);
      return parsed;
    }
  } catch {}
  return fallback;
};

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { properties: apiProperties, tenants: apiTenants, vendors: apiVendors, actionDeskTasks: apiActionDeskTasks, tickets: apiTickets, suggestions: apiSuggestions, isLoading: apiLoading, error: apiError, refresh: refreshData } = useApiData();

  // Re-fetch on route navigation
  const location = useLocation();
  const isFirstRender = useRef(true);
  useEffect(() => {
    if (isFirstRender.current) { isFirstRender.current = false; return; }
    refreshData();
  }, [location.pathname]);

  // Seed from localStorage so a page reload (e.g. iOS Safari evicting the tab from memory)
  // shows cached data immediately instead of a blank loading state. Dates are coerced back
  // from strings since JSON.parse can't reconstruct Date objects.
  const [properties, setProperties] = useState<Property[]>(() => loadFromStorage('rm_properties', []));
  const [tenants, setTenants] = useState<Tenant[]>(() => (loadFromStorage('rm_tenants', []) as Tenant[]).map(coerceTenant));
  const [vendors, setVendors] = useState<Vendor[]>(() => loadFromStorage('rm_vendors', []));
  const [suggestions, setSuggestions] = useState<Suggestion[]>(() => loadFromStorage('rm_suggestions', []));
  const [tickets, setTickets] = useState<MaintenanceTicket[]>(() => loadFromStorage('rm_tickets', []));
  const [actionDeskTasks, setActionDeskTasks] = useState<ActionDeskTask[]>(() => (loadFromStorage('rm_action_desk', []) as ActionDeskTask[]).map(coerceTask));

  useEffect(() => {
    if (apiLoading) return;
    if (apiError) {
      toast.error('Backend unavailable', {
        description: apiError,
        duration: 8000,
        id: 'backend-error', // deduplicate repeated toasts
      });
    } else {
      setProperties(apiProperties);
      setTenants(apiTenants);
      setVendors(apiVendors);
      setActionDeskTasks(apiActionDeskTasks);
      setTickets(apiTickets);
      setSuggestions(apiSuggestions);
      // Persist so the next page reload (e.g. iOS tab eviction) can show cached data.
      localStorage.setItem('rm_properties', JSON.stringify(apiProperties));
      localStorage.setItem('rm_tenants', JSON.stringify(apiTenants));
      localStorage.setItem('rm_vendors', JSON.stringify(apiVendors));
      localStorage.setItem('rm_action_desk', JSON.stringify(apiActionDeskTasks));
      localStorage.setItem('rm_tickets', JSON.stringify(apiTickets));
      localStorage.setItem('rm_suggestions', JSON.stringify(apiSuggestions));
    }
  }, [apiLoading, apiError, apiProperties, apiTenants, apiVendors, apiActionDeskTasks, apiTickets, apiSuggestions]);

  const [documents, setDocuments] = useState<ManagedDocument[]>([]);
  const [autonomySettings, setAutonomySettings] = useState<AutonomySettings>(() => loadFromStorage('rm_autonomy', defaultAutonomySettings));
  const [chatPanel, setChatPanel] = useState<ChatPanelState>({ isOpen: false, suggestionId: null, taskId: null, conversationId: null, pageContext: null });

  const [entityContext, setEntityContextState] = useState<Record<string, string>>(() => loadFromStorage('rm_entity_context', {}));

  // Persist non-API state to localStorage (settings, chat, documents, entity context).
  // API-backed state (properties/tenants/tasks/tickets/suggestions) is NOT persisted here —
  // the DB is the source of truth for those.
  useEffect(() => { localStorage.setItem('rm_autonomy', JSON.stringify(autonomySettings)); }, [autonomySettings]);
  useEffect(() => { localStorage.setItem('rm_entity_context', JSON.stringify(entityContext)); }, [entityContext]);

  const getEntityContext = useCallback((entityId: string) => entityContext[entityId] || '', [entityContext]);
  const setEntityContext = useCallback((entityId: string, context: string) => {
    setEntityContextState(prev => ({ ...prev, [entityId]: context }));
  }, []);

  const updateSuggestionStatus = useCallback((id: string, status: SuggestionStatus) => {
    setSuggestions(prev => prev.map(s => s.id === id ? { ...s, status } : s));
  }, []);

  const updateSuggestion = useCallback((id: string, updates: Partial<Suggestion>) => {
    setSuggestions(prev => prev.map(s => s.id === id ? { ...s, ...updates } : s));
  }, []);

  const addChatMessage = useCallback((context: { suggestionId?: string | null; taskId?: string | null }, message: ChatMessage) => {
    if (context.taskId) {
      setActionDeskTasks(prev => prev.map(t =>
        t.id === context.taskId ? { ...t, chatThread: [...t.chatThread, message] } : t
      ));
    } else if (context.suggestionId) {
      setSuggestions(prev => prev.map(s =>
        s.id === context.suggestionId ? { ...s, chatThread: [...s.chatThread, message] } : s
      ));
    }
  }, []);

  const updateTaskMessage = useCallback((taskId: string, messageId: string, updates: Partial<ChatMessage>) => {
    setActionDeskTasks(prev => prev.map(t =>
      t.id === taskId
        ? { ...t, chatThread: t.chatThread.map(m => m.id === messageId ? { ...m, ...updates } : m) }
        : t
    ));
  }, []);

  const setTaskMessages = useCallback((taskId: string, messages: ChatMessage[]) => {
    setActionDeskTasks(prev => prev.map(t =>
      t.id === taskId ? { ...t, chatThread: messages } : t
    ));
  }, []);

  const addProperty = useCallback((prop: Property) => {
    setProperties(prev => [prop, ...prev]);
  }, []);

  const updateProperty = useCallback((id: string, updates: Partial<Property>) => {
    setProperties(prev => prev.map(p => p.id === id ? { ...p, ...updates } : p));
  }, []);

  const removeProperty = useCallback((id: string) => {
    setProperties(prev => prev.filter(p => p.id !== id));
  }, []);

  const addTenant = useCallback((tenant: Tenant) => {
    setTenants(prev => [tenant, ...prev]);
  }, []);

  const updateTenant = useCallback((id: string, updates: Partial<Tenant>) => {
    setTenants(prev => prev.map(t => t.id === id ? { ...t, ...updates } : t));
  }, []);

  const addVendor = useCallback((vendor: Vendor) => {
    setVendors(prev => [vendor, ...prev]);
  }, []);

  const updateVendor = useCallback((id: string, updates: Partial<Vendor>) => {
    setVendors(prev => prev.map(v => v.id === id ? { ...v, ...updates } : v));
  }, []);

  const removeVendor = useCallback((id: string) => {
    setVendors(prev => prev.filter(v => v.id !== id));
  }, []);

  const addDocument = useCallback((doc: ManagedDocument) => {
    setDocuments(prev => {
      const exists = prev.find(d => d.id === doc.id);
      if (exists) return prev.map(d => d.id === doc.id ? doc : d);
      return [...prev, doc];
    });
  }, []);

  const updateDocument = useCallback((id: string, updates: Partial<ManagedDocument>) => {
    setDocuments(prev => prev.map(d => d.id === id ? { ...d, ...updates } : d));
  }, []);

  const replaceDocument = useCallback((oldId: string, doc: ManagedDocument) => {
    setDocuments(prev => prev.map(d => d.id === oldId ? doc : d));
  }, []);

  const removeDocument = useCallback((id: string) => {
    setDocuments(prev => prev.filter(d => d.id !== id));
  }, []);

  const addTask = useCallback((task: ActionDeskTask) => {
    setActionDeskTasks(prev => [task, ...prev]);
  }, []);

  const removeTask = useCallback((taskId: string) => {
    setActionDeskTasks(prev => prev.filter(t => t.id !== taskId));
  }, []);

  const updateTask = useCallback((taskId: string, updates: Partial<ActionDeskTask>) => {
    // Persist mode/status changes to backend (fire-and-forget)
    if (updates.mode !== undefined || updates.status !== undefined) {
      graphqlQuery(UPDATE_TASK_MUTATION, {
        input: {
          uid: taskId,
          taskMode: updates.mode ?? null,
          taskStatus: updates.status ?? null,
        }
      }).catch((err: Error) => console.warn('Failed to update task:', err));
    }
    setActionDeskTasks(prev => prev.map(t => t.id === taskId ? { ...t, ...updates } : t));
  }, []);

  const openChat = useCallback((opts?: { suggestionId?: string | null; taskId?: string | null; pageContext?: string | null; conversationId?: string | null; lazy?: boolean }) => {
    if (opts?.taskId || opts?.suggestionId) {
      setChatPanel({
        isOpen: true,
        taskId: opts?.taskId ?? null,
        suggestionId: opts?.suggestionId ?? null,
        conversationId: null,
        pageContext: opts?.pageContext ?? null,
      });
      return;
    }

    if (opts?.conversationId) {
      setChatPanel({
        isOpen: true,
        taskId: null,
        suggestionId: null,
        conversationId: opts.conversationId,
        pageContext: opts?.pageContext ?? null,
      });
      return;
    }

    // Lazy mode: just open the panel without creating a conversation.
    // The backend will auto-create one when the user sends a message.
    if (opts?.lazy) {
      setChatPanel({
        isOpen: true,
        taskId: null,
        suggestionId: null,
        conversationId: null,
        pageContext: opts?.pageContext ?? null,
      });
      return;
    }

    // Explicit new chat — create a DB conversation
    const t = getToken();
    fetch('/chat/new', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(t ? { Authorization: `Bearer ${t}` } : {}) },
    })
      .then(r => r.json())
      .then(data => {
        setChatPanel({
          isOpen: true,
          taskId: null,
          suggestionId: null,
          conversationId: data.id,
          pageContext: opts?.pageContext ?? null,
        });
      })
      .catch(() => {});
  }, []);

  const closeChat = useCallback(() => {
    setChatPanel({ isOpen: false, suggestionId: null, taskId: null, conversationId: null, pageContext: null });
  }, []);

  const setChatConversationId = useCallback((id: string) => {
    setChatPanel(prev => ({ ...prev, conversationId: id }));
  }, []);

  return (
    <AppContext.Provider value={{
      properties, tenants, vendors, suggestions, tickets, actionDeskTasks, isLoading: apiLoading && actionDeskTasks.length === 0 && properties.length === 0, documents, autonomySettings,
      chatPanel, entityContext, getEntityContext, setEntityContext,
      updateSuggestionStatus, updateSuggestion, addChatMessage, updateTaskMessage, setTaskMessages, updateTask,
      addTask, removeTask,
      addProperty, updateProperty, removeProperty, addTenant, updateTenant, addVendor, updateVendor, removeVendor, addDocument, updateDocument, replaceDocument, removeDocument, openChat, setChatConversationId, closeChat, setAutonomySettings, refreshData,
    }}>
      {children}
    </AppContext.Provider>
  );
};

export const useApp = () => {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used within AppProvider');
  return ctx;
};
