import { useState, useEffect, useCallback } from 'react';
import { graphqlQuery, HOUSES_QUERY, TENANTS_QUERY, TASKS_QUERY } from '@/data/api';
import { Property, Tenant, ActionDeskTask, MaintenanceTicket, Suggestion, ChatMessage, TaskParticipant } from '@/data/mockData';

interface ApiState {
  properties: Property[];
  tenants: Tenant[];
  actionDeskTasks: ActionDeskTask[];
  tickets: MaintenanceTicket[];
  suggestions: Suggestion[];
  isLoading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useApiData(): ApiState {
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey(k => k + 1), []);

  const [state, setState] = useState<Omit<ApiState, 'refresh'>>({
    properties: [],
    tenants: [],
    actionDeskTasks: [],
    tickets: [],
    suggestions: [],
    isLoading: true,
    error: null,
  });

  // Re-fetch when window regains focus or tab becomes visible
  useEffect(() => {
    const handleFocus = () => setRefreshKey(k => k + 1);
    window.addEventListener('focus', handleFocus);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') setRefreshKey(k => k + 1);
    });
    return () => window.removeEventListener('focus', handleFocus);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function fetchAll() {
      const [housesResult, tenantsResult, tasksResult] = await Promise.allSettled([
        graphqlQuery<{ houses: ApiHouse[] }>(HOUSES_QUERY),
        graphqlQuery<{ tenants: ApiTenant[] }>(TENANTS_QUERY),
        graphqlQuery<{ tasks: ApiTask[] }>(TASKS_QUERY),
      ]);

      if (cancelled) return;

      const properties: Property[] = housesResult.status === 'fulfilled'
        ? (housesResult.value.houses || []).map(h => ({
            id: h.uid,
            name: h.name,
            address: h.address,
            propertyType: (h.propertyType ?? 'multi_family') as 'single_family' | 'multi_family',
            source: (h.source ?? 'manual') as 'manual' | 'document',
            units: h.units ?? 0,
            occupiedUnits: h.occupiedUnits ?? 0,
            monthlyRevenue: h.monthlyRevenue ?? 0,
            unitList: h.unitList?.map(u => ({ id: u.uid, label: u.label, isOccupied: u.isOccupied })),
          }))
        : [];

      const tenants: Tenant[] = tenantsResult.status === 'fulfilled'
        ? (tenantsResult.value.tenants || []).map(t => ({
            id: t.uid,
            name: t.name,
            email: t.email ?? '',
            unit: t.unitLabel ?? '',
            propertyId: t.rents?.[0]?.uid ?? '',
            leaseEnd: t.leaseEndDate ? new Date(t.leaseEndDate) : new Date(),
            rentAmount: t.rentAmount ?? 0,
            paymentStatus: (t.paymentStatus as Tenant['paymentStatus']) ?? 'current',
            isActive: t.isActive ?? false,
          }))
        : [];

      const allTasks = tasksResult.status === 'fulfilled' ? (tasksResult.value.tasks || []) : [];

      // Action Desk: all tasks except dismissed (suggested → active with waiting_approval mode)
      const actionDeskTasks: ActionDeskTask[] = allTasks
        .filter(t => t.taskStatus !== 'dismissed')
        .map(apiTaskToActionDesk);

      // Maintenance tickets: category=maintenance tasks
      const tickets: MaintenanceTicket[] = allTasks
        .filter(t => t.category === 'maintenance')
        .map(apiTaskToTicket);

      // Suggestions: status=suggested tasks
      const suggestions: Suggestion[] = allTasks
        .filter(t => t.taskStatus === 'suggested')
        .map(apiTaskToSuggestion);

      // Only set error if all three queries failed
      const allFailed = housesResult.status === 'rejected' && tenantsResult.status === 'rejected' && tasksResult.status === 'rejected';
      const firstError = allFailed ? (housesResult.reason as Error).message : null;

      setState({ properties, tenants, actionDeskTasks, tickets, suggestions, isLoading: false, error: firstError });
    }

    fetchAll();
    return () => { cancelled = true; };
  }, [refreshKey]);

  return { ...state, refresh };
}

// --- API → local type mappers ---

// Backend returns UTC timestamps without 'Z'. JS parses tz-naive date-time strings
// as local time, so we append 'Z' to force correct UTC interpretation.
function parseUtc(iso: string): Date {
  if (!iso) return new Date();
  return new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
}

export function apiMessagesToChatThread(messages: ApiTaskMessage[]): ChatMessage[] {
  return messages.map(m => ({
    id: m.uid,
    role: m.isAi || m.isSystem ? 'assistant' as const : 'user' as const,
    content: m.body ?? '',
    timestamp: parseUtc(m.sentAt),
    senderName: m.senderName ?? undefined,
    messageType: (m.messageType as ChatMessage['messageType']) ?? 'message',
    draftReply: m.draftReply ?? undefined,
    approvalStatus: (m.approvalStatus as ChatMessage['approvalStatus']) ?? undefined,
    relatedTasks: m.relatedTaskIds ?? undefined,
  }));
}

function apiTaskParticipants(t: ApiTask): TaskParticipant[] {
  const seen = new Set<string>();
  const participants: TaskParticipant[] = [];

  // Always include AI agent
  participants.push({ type: 'agent', name: 'RentMate AI' });
  seen.add('RentMate AI');

  // Add unique non-AI senders from messages
  for (const m of t.messages ?? []) {
    if (m.isAi || !m.senderName || seen.has(m.senderName)) continue;
    seen.add(m.senderName);
    // Try to infer type from context
    if (t.tenantName && m.senderName.includes(t.tenantName.split(' ')[0])) {
      participants.push({ type: 'tenant', name: m.senderName });
    } else if (t.vendorAssigned && m.senderName.includes(t.vendorAssigned.split(' ')[0])) {
      participants.push({ type: 'vendor', name: m.senderName });
    }
  }
  return participants;
}

function apiTaskToActionDesk(t: ApiTask): ActionDeskTask {
  const thread = apiMessagesToChatThread(t.messages ?? []);
  const last = thread[thread.length - 1];
  return {
    id: t.uid,
    title: t.title ?? '(untitled)',
    mode: (t.taskMode as ActionDeskTask['mode']) ?? 'manual',
    status: (t.taskStatus === 'suggested' ? 'active' : (t.taskStatus as ActionDeskTask['status'])) ?? 'active',
    participants: apiTaskParticipants(t),
    lastMessage: last?.content ?? '',
    lastMessageBy: last?.senderName ?? '',
    lastMessageAt: last ? new Date(last.timestamp) : parseUtc(t.createdAt),
    unreadCount: 0,
    propertyId: t.propertyId ?? undefined,
    category: (t.category as ActionDeskTask['category']) ?? 'maintenance',
    urgency: (t.urgency as ActionDeskTask['urgency']) ?? 'low',
    chatThread: thread,
    confidential: t.confidential ?? false,
  };
}

function apiTaskToTicket(t: ApiTask): MaintenanceTicket {
  return {
    id: t.uid,
    tenantId: '',
    tenantName: t.tenantName ?? '',
    propertyId: t.propertyId ?? '',
    unit: t.unitLabel ?? '',
    description: t.title ?? '',
    priority: (t.priority as MaintenanceTicket['priority']) ?? 'routine',
    status: ticketStatusFromTaskStatus(t.taskStatus),
    createdAt: new Date(t.createdAt),
    aiTriageSuggestion: t.aiTriageSuggestion ?? undefined,
    vendorAssigned: t.vendorAssigned ?? undefined,
  };
}

function ticketStatusFromTaskStatus(s: string | undefined): MaintenanceTicket['status'] {
  if (s === 'resolved') return 'resolved';
  if (s === 'cancelled') return 'closed';
  if (s === 'paused') return 'in_progress';
  return 'open';
}

function apiTaskToSuggestion(t: ApiTask): Suggestion {
  return {
    id: t.uid,
    category: (t.category as Suggestion['category']) ?? 'maintenance',
    urgency: (t.urgency as Suggestion['urgency']) ?? 'low',
    title: t.title ?? '',
    description: '',
    recommendedAction: '',
    confidence: 0.8,
    autonomyLevel: 'suggest',
    status: 'pending',
    propertyId: t.propertyId ?? undefined,
    createdAt: new Date(t.createdAt),
    chatThread: apiMessagesToChatThread(t.messages ?? []),
  };
}

// --- API response interfaces ---

interface ApiHouseUnit {
  uid: string;
  label: string;
  isOccupied: boolean;
}

interface ApiHouse {
  uid: string;
  name: string;
  address: string;
  propertyType?: string;
  source?: string;
  units?: number;
  occupiedUnits?: number;
  monthlyRevenue?: number;
  unitList?: ApiHouseUnit[];
}

interface ApiTenant {
  uid: string;
  name: string;
  email?: string;
  unitLabel?: string;
  leaseEndDate?: string;
  rentAmount?: number;
  paymentStatus?: string;
  isActive?: boolean;
  rents?: { uid: string }[];
}

interface ApiTaskMessage {
  uid: string;
  body?: string;
  messageType?: string;
  senderName?: string;
  isAi: boolean;
  isSystem: boolean;
  draftReply?: string;
  approvalStatus?: string;
  relatedTaskIds?: { taskId: string; label: string }[];
  sentAt: string;
}

interface ApiTask {
  uid: string;
  title?: string;
  isTask: boolean;
  taskStatus?: string;
  taskMode?: string;
  source?: string;
  category?: string;
  urgency?: string;
  priority?: string;
  confidential?: boolean;
  lastMessageAt?: string;
  propertyId?: string;
  unitId?: string;
  createdAt: string;
  messages?: ApiTaskMessage[];
  tenantName?: string;
  unitLabel?: string;
  aiTriageSuggestion?: string;
  vendorAssigned?: string;
}
