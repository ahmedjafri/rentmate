// Entity context management for properties, tenants, and other entities
import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import {
  Suggestion, Property, Tenant, Vendor, MaintenanceTicket, AutonomySettings, ChatMessage, ActionDeskTask, ManagedDocument,
  defaultAutonomySettings,
  SuggestionStatus, ActionDeskTask as ADT,
} from '@/data/mockData';
import { useApiData } from '@/hooks/useApiData';
import { graphqlQuery, SEND_MESSAGE_MUTATION, UPDATE_TASK_MUTATION } from '@/data/api';
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

export interface ChatSession {
  id: string;           // 'general' for the default session, uuid for context sessions
  title: string;        // displayed name in the Chats list
  contextKey: string | null;  // e.g. 'property:abc123' — used for deduplication
  pageContext: string | null; // full context block sent to the AI
  messages: ChatMessage[];
  lastMessageAt: Date | null;
}

interface ChatPanelState {
  isOpen: boolean;
  suggestionId: string | null;
  taskId: string | null;
  sessionId: string | null;
  conversationId: string | null;  // DB-backed conversation ID
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
  chatSessions: ChatSession[];
  entityContext: Record<string, string>;
  getEntityContext: (entityId: string) => string;
  setEntityContext: (entityId: string, context: string) => void;
  updateSuggestionStatus: (id: string, status: SuggestionStatus) => void;
  updateSuggestion: (id: string, updates: Partial<Suggestion>) => void;
  addChatMessage: (context: { suggestionId?: string | null; taskId?: string | null; sessionId?: string | null }, message: ChatMessage) => void;
  updateTaskMessage: (taskId: string, messageId: string, updates: Partial<ChatMessage>) => void;
  setTaskMessages: (taskId: string, messages: ChatMessage[]) => void;
  updateTask: (taskId: string, updates: Partial<ActionDeskTask>) => void;
  removeTask: (taskId: string) => void;
  addTask: (task: ActionDeskTask) => void;
  addProperty: (prop: Property) => void;
  updateProperty: (id: string, updates: Partial<Property>) => void;
  removeProperty: (id: string) => void;
  addTenant: (tenant: Tenant) => void;
  addVendor: (vendor: Vendor) => void;
  updateVendor: (id: string, updates: Partial<Vendor>) => void;
  removeVendor: (id: string) => void;
  addDocument: (doc: ManagedDocument) => void;
  updateDocument: (id: string, updates: Partial<ManagedDocument>) => void;
  replaceDocument: (oldId: string, doc: ManagedDocument) => void;
  removeDocument: (id: string) => void;
  openChat: (opts?: { suggestionId?: string | null; taskId?: string | null; pageContext?: string | null; contextKey?: string | null; sessionTitle?: string | null; conversationId?: string | null; conversationType?: string | null }) => void;
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
  const [chatPanel, setChatPanel] = useState<ChatPanelState>({ isOpen: false, suggestionId: null, taskId: null, sessionId: null, conversationId: null, pageContext: null });

  const [chatSessions, setChatSessions] = useState<ChatSession[]>(() => {
    const stored = loadFromStorage('rm_chat_sessions', null) as ChatSession[] | null;
    if (stored && Array.isArray(stored) && stored.length > 0) {
      return stored.map(s => ({
        ...s,
        messages: (s.messages ?? []).map(m => ({
          ...m,
          timestamp: m.timestamp instanceof Date ? m.timestamp : new Date(m.timestamp as unknown as string),
        })),
        lastMessageAt: s.lastMessageAt ? new Date(s.lastMessageAt as unknown as string) : null,
      }));
    }
    // Migrate legacy single global thread to a 'general' session
    const legacy = loadFromStorage('rm_global_chat', []) as ChatMessage[];
    if (legacy.length > 0) {
      const msgs = legacy.map(m => ({ ...m, timestamp: m.timestamp instanceof Date ? m.timestamp : new Date(m.timestamp as unknown as string) }));
      return [{ id: 'general', title: 'Ask RentMate', contextKey: null, pageContext: null, messages: msgs, lastMessageAt: msgs[msgs.length - 1].timestamp }];
    }
    return [];
  });
  const chatSessionsRef = useRef(chatSessions);
  useEffect(() => { chatSessionsRef.current = chatSessions; }, [chatSessions]);

  const [entityContext, setEntityContextState] = useState<Record<string, string>>(() => loadFromStorage('rm_entity_context', {}));

  // Persist non-API state to localStorage (settings, chat, documents, entity context).
  // API-backed state (properties/tenants/tasks/tickets/suggestions) is NOT persisted here —
  // the DB is the source of truth for those.
  useEffect(() => { localStorage.setItem('rm_autonomy', JSON.stringify(autonomySettings)); }, [autonomySettings]);
  useEffect(() => { localStorage.setItem('rm_chat_sessions', JSON.stringify(chatSessions)); }, [chatSessions]);
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

  const addChatMessage = useCallback((context: { suggestionId?: string | null; taskId?: string | null; sessionId?: string | null }, message: ChatMessage) => {
    // Persist manager messages on tasks to backend (fire-and-forget)
    if (context.taskId && message.role === 'user') {
      const task = actionDeskTasks.find(t => t.id === context.taskId);
      if (task?.aiConversationId) {
        graphqlQuery(SEND_MESSAGE_MUTATION, {
          input: {
            conversationId: task.aiConversationId,
            body: message.content,
            // 'message' type goes to the AI thread — store as 'thread' so the Chat tab
            // (which shows participant messages) doesn't surface these.
            messageType: message.messageType === 'message' ? 'thread' : (message.messageType ?? 'thread'),
            senderName: message.senderName ?? 'You',
            isAi: false,
          }
        }).catch((err: Error) => console.warn('Failed to persist task message:', err));
      }
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
      // Add to the named session (or 'general' if no sessionId)
      const targetId = context.sessionId ?? 'general';
      setChatSessions(prev => {
        const exists = prev.find(s => s.id === targetId);
        if (exists) {
          return prev.map(s => s.id === targetId
            ? { ...s, messages: [...s.messages, message], lastMessageAt: message.timestamp }
            : s
          );
        }
        // Shouldn't happen, but create a fallback general session
        return [...prev, {
          id: targetId,
          title: 'Ask RentMate',
          contextKey: null,
          pageContext: null,
          messages: [message],
          lastMessageAt: message.timestamp,
        }];
      });
    }
  }, [actionDeskTasks]);

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

  const openChat = useCallback((opts?: { suggestionId?: string | null; taskId?: string | null; pageContext?: string | null; contextKey?: string | null; sessionTitle?: string | null; conversationId?: string | null; conversationType?: string | null }) => {
    // Task/suggestion threads don't use the session system
    if (opts?.taskId || opts?.suggestionId) {
      setChatPanel({
        isOpen: true,
        taskId: opts?.taskId ?? null,
        suggestionId: opts?.suggestionId ?? null,
        sessionId: null,
        conversationId: null,
        pageContext: opts?.pageContext ?? null,
      });
      return;
    }

    // DB-backed conversation (from Chats page or tenant chat)
    if (opts?.conversationId) {
      setChatPanel({
        isOpen: true,
        taskId: null,
        suggestionId: null,
        sessionId: null,
        conversationId: opts.conversationId,
        pageContext: null,
      });
      return;
    }

    const contextKey = opts?.contextKey ?? null;
    const sessions = chatSessionsRef.current;

    // Find existing session by contextKey (or 'general' for null contextKey)
    const existing = contextKey !== null
      ? sessions.find(s => s.contextKey === contextKey)
      : sessions.find(s => s.id === 'general');

    if (existing) {
      setChatPanel({ isOpen: true, taskId: null, suggestionId: null, sessionId: existing.id, conversationId: null, pageContext: existing.pageContext });
      return;
    }

    // Create a new session
    // crypto.randomUUID() requires a secure context (HTTPS / localhost).
    // Fall back to getRandomValues-based generation so the app works over
    // plain HTTP on a LAN IP address as well.
    const newId = contextKey ?? 'general';
    const newSession: ChatSession = {
      id: newId === 'general' ? 'general' : generateSessionId(),
      title: opts?.sessionTitle ?? 'Ask RentMate',
      contextKey,
      pageContext: opts?.pageContext ?? null,
      messages: [],
      lastMessageAt: null,
    };
    setChatSessions(prev => [...prev, newSession]);
    setChatPanel({ isOpen: true, taskId: null, suggestionId: null, sessionId: newSession.id, conversationId: null, pageContext: newSession.pageContext });
  }, []);

  const closeChat = useCallback(() => {
    setChatPanel({ isOpen: false, suggestionId: null, taskId: null, sessionId: null, conversationId: null, pageContext: null });
  }, []);

  return (
    <AppContext.Provider value={{
      properties, tenants, vendors, suggestions, tickets, actionDeskTasks, isLoading: apiLoading && actionDeskTasks.length === 0 && properties.length === 0, documents, autonomySettings,
      chatPanel, chatSessions, entityContext, getEntityContext, setEntityContext,
      updateSuggestionStatus, updateSuggestion, addChatMessage, updateTaskMessage, setTaskMessages, updateTask,
      addTask, removeTask,
      addProperty, updateProperty, removeProperty, addTenant, addVendor, updateVendor, removeVendor, addDocument, updateDocument, replaceDocument, removeDocument, openChat, closeChat, setAutonomySettings, refreshData,
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
