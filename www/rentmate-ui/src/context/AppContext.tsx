// Entity context management for properties, tenants, and other entities
import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import {
  Suggestion, Property, Tenant, MaintenanceTicket, AutonomySettings, ChatMessage, ActionDeskTask, ManagedDocument,
  defaultAutonomySettings,
  SuggestionStatus, ActionDeskTask as ADT,
} from '@/data/mockData';
import { useApiData } from '@/hooks/useApiData';
import { graphqlQuery, ADD_TASK_MESSAGE_MUTATION, UPDATE_TASK_MUTATION } from '@/data/api';
import { toast } from 'sonner';

interface ChatPanelState {
  isOpen: boolean;
  suggestionId: string | null;
  taskId: string | null;
}

interface AppContextType {
  properties: Property[];
  tenants: Tenant[];
  suggestions: Suggestion[];
  tickets: MaintenanceTicket[];
  actionDeskTasks: ActionDeskTask[];
  isLoading: boolean;
  documents: ManagedDocument[];
  autonomySettings: AutonomySettings;
  chatPanel: ChatPanelState;
  globalChatThread: ChatMessage[];
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
  addDocument: (doc: ManagedDocument) => void;
  updateDocument: (id: string, updates: Partial<ManagedDocument>) => void;
  replaceDocument: (oldId: string, doc: ManagedDocument) => void;
  removeDocument: (id: string) => void;
  openChat: (opts?: { suggestionId?: string | null; taskId?: string | null }) => void;
  closeChat: () => void;
  setAutonomySettings: (settings: AutonomySettings) => void;
}

const AppContext = createContext<AppContextType | undefined>(undefined);

// One-time cleanup: remove localStorage keys that used to cache API-backed data.
// The DB is now the source of truth; stale cached copies cause mock data to bleed through.
['rm_properties', 'rm_tenants', 'rm_suggestions', 'rm_tickets', 'rm_action_desk', 'rm_documents'].forEach(k => localStorage.removeItem(k));

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
  const { properties: apiProperties, tenants: apiTenants, actionDeskTasks: apiActionDeskTasks, tickets: apiTickets, suggestions: apiSuggestions, isLoading: apiLoading, error: apiError } = useApiData();

  // Start empty — API data replaces these once the first successful fetch completes.
  // We do NOT seed from localStorage/mock here because that causes stale mock data to
  // persist whenever the DB has fewer records than the mock set.
  const [properties, setProperties] = useState<Property[]>([]);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [tickets, setTickets] = useState<MaintenanceTicket[]>([]);
  const [actionDeskTasks, setActionDeskTasks] = useState<ActionDeskTask[]>([]);

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
      setActionDeskTasks(apiActionDeskTasks);
      setTickets(apiTickets);
      setSuggestions(apiSuggestions);
    }
  }, [apiLoading, apiError, apiProperties, apiTenants, apiActionDeskTasks, apiTickets, apiSuggestions]);
  const [documents, setDocuments] = useState<ManagedDocument[]>([]);
  const [autonomySettings, setAutonomySettings] = useState<AutonomySettings>(() => loadFromStorage('rm_autonomy', defaultAutonomySettings));
  const [chatPanel, setChatPanel] = useState<ChatPanelState>({ isOpen: false, suggestionId: null, taskId: null });
  const [globalChatThread, setGlobalChatThread] = useState<ChatMessage[]>(() => loadFromStorage('rm_global_chat', []));
  const [entityContext, setEntityContextState] = useState<Record<string, string>>(() => loadFromStorage('rm_entity_context', {}));

  // Persist non-API state to localStorage (settings, chat, documents, entity context).
  // API-backed state (properties/tenants/tasks/tickets/suggestions) is NOT persisted here —
  // the DB is the source of truth for those.
  useEffect(() => { localStorage.setItem('rm_autonomy', JSON.stringify(autonomySettings)); }, [autonomySettings]);
  useEffect(() => { localStorage.setItem('rm_global_chat', JSON.stringify(globalChatThread)); }, [globalChatThread]);
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
    // Persist manager messages on tasks to backend (fire-and-forget)
    if (context.taskId && message.role === 'user') {
      graphqlQuery(ADD_TASK_MESSAGE_MUTATION, {
        input: {
          taskId: context.taskId,
          body: message.content,
          messageType: message.messageType ?? 'message',
          senderName: message.senderName ?? 'You',
          isAi: false,
        }
      }).catch((err: Error) => console.warn('Failed to persist task message:', err));
    }

    if (context.taskId) {
      setActionDeskTasks(prev => prev.map(t =>
        t.id === context.taskId ? { ...t, chatThread: [...t.chatThread, message] } : t
      ));
    } else if (context.suggestionId) {
      setSuggestions(prev => prev.map(s =>
        s.id === context.suggestionId ? { ...s, chatThread: [...s.chatThread, message] } : s
      ));
    } else {
      setGlobalChatThread(prev => [...prev, message]);
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

  const openChat = useCallback((opts?: { suggestionId?: string | null; taskId?: string | null }) => {
    setChatPanel({
      isOpen: true,
      suggestionId: opts?.suggestionId ?? null,
      taskId: opts?.taskId ?? null,
    });
  }, []);

  const closeChat = useCallback(() => {
    setChatPanel({ isOpen: false, suggestionId: null, taskId: null });
  }, []);

  return (
    <AppContext.Provider value={{
      properties, tenants, suggestions, tickets, actionDeskTasks, isLoading: apiLoading, documents, autonomySettings,
      chatPanel, globalChatThread, entityContext, getEntityContext, setEntityContext,
      updateSuggestionStatus, updateSuggestion, addChatMessage, updateTaskMessage, setTaskMessages, updateTask,
      addTask, removeTask,
      addProperty, updateProperty, removeProperty, addTenant, addDocument, updateDocument, replaceDocument, removeDocument, openChat, closeChat, setAutonomySettings,
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
