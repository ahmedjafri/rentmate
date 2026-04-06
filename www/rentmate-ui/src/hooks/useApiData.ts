import { useState, useEffect, useCallback } from 'react';
import { graphqlQuery, HOUSES_QUERY, TENANTS_QUERY, TASKS_QUERY, VENDORS_QUERY, SUGGESTIONS_QUERY } from '@/data/api';
import { Property, Tenant, Vendor, ActionDeskTask, MaintenanceTicket, Suggestion, ChatMessage, TaskParticipant } from '@/data/mockData';

interface ApiState {
  properties: Property[];
  tenants: Tenant[];
  vendors: Vendor[];
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
    vendors: [],
    actionDeskTasks: [],
    tickets: [],
    suggestions: [],
    isLoading: true,
    error: null,
  });

  // Re-fetch when the tab becomes visible after being hidden for a while.
  // Only refresh if the page was hidden for more than 5 minutes — avoids reloading
  // on every app-switch on mobile Safari.
  useEffect(() => {
    let hiddenAt: number | null = null;
    const STALE_MS = 5 * 60 * 1000;

    const handleVisibility = () => {
      if (document.visibilityState === 'hidden') {
        hiddenAt = Date.now();
      } else if (document.visibilityState === 'visible') {
        if (hiddenAt !== null && Date.now() - hiddenAt > STALE_MS) {
          setRefreshKey(k => k + 1);
        }
        hiddenAt = null;
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);
    return () => document.removeEventListener('visibilitychange', handleVisibility);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function fetchAll() {
      const [housesResult, tenantsResult, tasksResult, vendorsResult, suggestionsResult] = await Promise.allSettled([
        graphqlQuery<{ houses: ApiHouse[] }>(HOUSES_QUERY),
        graphqlQuery<{ tenants: ApiTenant[] }>(TENANTS_QUERY),
        graphqlQuery<{ tasks: ApiTask[] }>(TASKS_QUERY),
        graphqlQuery<{ vendors: ApiVendor[] }>(VENDORS_QUERY),
        graphqlQuery<{ suggestions: ApiSuggestion[] }>(SUGGESTIONS_QUERY, {}),
      ]);

      if (cancelled) return;

      const properties: Property[] = housesResult.status === 'fulfilled'
        ? (housesResult.value.houses || []).map(h => ({
            id: h.uid,
            name: h.name,
            address: h.address,
            propertyType: (h.propertyType ?? 'multi_family') as 'single_family' | 'multi_family',
            source: (h.source ?? 'manual') as 'manual' | 'document',
            context: h.context,
            units: h.units ?? 0,
            occupiedUnits: h.occupiedUnits ?? 0,
            monthlyRevenue: h.monthlyRevenue ?? 0,
            unitList: h.unitList?.map(u => ({ id: u.uid, label: u.label, isOccupied: u.isOccupied, context: u.context })),
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
            context: t.context,
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

      // Suggestions: fetched from dedicated suggestions query
      const suggestions: Suggestion[] = suggestionsResult.status === 'fulfilled'
        ? (suggestionsResult.value.suggestions || []).map(apiSuggestionToSuggestion)
        : [];

      const vendors: Vendor[] = vendorsResult.status === 'fulfilled'
        ? (vendorsResult.value.vendors || []).map(v => ({
            id: v.uid,
            name: v.name,
            company: v.company,
            vendorType: v.vendorType,
            phone: v.phone,
            email: v.email,
            notes: v.notes,
            context: v.context,
            portalUrl: v.portalUrl,
          }))
        : [];

      // Only set error if all three queries failed
      const allFailed = housesResult.status === 'rejected' && tenantsResult.status === 'rejected' && tasksResult.status === 'rejected';
      const firstError = allFailed ? (housesResult.reason as Error).message : null;

      setState({ properties, tenants, vendors, actionDeskTasks, tickets, suggestions, isLoading: false, error: firstError });
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
  const mapped = messages.map(m => ({
    id: m.uid,
    role: (m.isAi || m.isSystem ? 'assistant' : 'user') as 'assistant' | 'user',
    content: m.body ?? '',
    timestamp: parseUtc(m.sentAt),
    senderName: m.senderName ?? undefined,
    messageType: (m.messageType as ChatMessage['messageType']) ?? 'message',
    draftReply: m.draftReply ?? undefined,
    approvalStatus: (m.approvalStatus as ChatMessage['approvalStatus']) ?? undefined,
    suggestionId: m.suggestionId ?? undefined,
    relatedTasks: m.relatedTaskIds ?? undefined,
  }));
  // Pending approval messages should appear after other messages so they
  // don't sit at the top of the thread above the actual conversation.
  const isPendingSuggestion = (m: ChatMessage) => (m.messageType === 'approval' || m.messageType === 'suggestion') && (!m.approvalStatus || m.approvalStatus === 'pending');
  const regular = mapped.filter(m => !isPendingSuggestion(m));
  const pendingApprovals = mapped.filter(isPendingSuggestion);
  return [...regular, ...pendingApprovals];
}

function apiTaskParticipants(t: ApiTask): TaskParticipant[] {
  const seen = new Set<string>();
  const participants: TaskParticipant[] = [];

  // Always include AI agent
  participants.push({ type: 'agent', name: 'RentMate AI' });
  seen.add('RentMate AI');

  // Add assigned vendor directly — don't require them to have sent a message yet
  if (t.assignedVendorName) {
    participants.push({ type: 'vendor', name: t.assignedVendorName, id: t.assignedVendorId ?? undefined });
    seen.add(t.assignedVendorName);
  }

  // Add unique non-AI senders from messages
  for (const m of t.messages ?? []) {
    if (m.isAi || !m.senderName || seen.has(m.senderName)) continue;
    seen.add(m.senderName);
    // Try to infer type from context
    if (t.tenantName && m.senderName.includes(t.tenantName.split(' ')[0])) {
      participants.push({ type: 'tenant', name: m.senderName });
    } else {
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
    taskNumber: t.taskNumber ?? null,
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
    requireVendorType: t.requireVendorType,
    assignedVendorId: t.assignedVendorId,
    assignedVendorName: t.assignedVendorName,
    steps: t.steps ?? undefined,
    suggestionOptions: t.suggestionOptions ?? undefined,
    aiConversationId: t.aiConversationId ?? null,
    externalConversationId: t.externalConversationId ?? null,
    parentConversationId: t.externalConversationId ?? t.parentConversationId ?? null,
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

function apiSuggestionToSuggestion(s: ApiSuggestion): Suggestion {
  return {
    id: s.uid,
    title: s.title ?? '',
    body: s.body ?? undefined,
    category: (s.category as Suggestion['category']) ?? 'maintenance',
    urgency: (s.urgency as Suggestion['urgency']) ?? 'low',
    status: s.status === 'pending' ? 'pending' : s.status === 'accepted' ? 'accepted' : 'dismissed',
    source: s.source ?? undefined,
    automationKey: s.automationKey ?? undefined,
    options: s.options ?? undefined,
    actionTaken: s.actionTaken ?? undefined,
    propertyId: s.propertyId ?? undefined,
    unitId: s.unitId ?? undefined,
    taskId: s.taskId ?? undefined,
    vendorName: s.vendorName ?? undefined,
    propertyName: s.propertyName ?? undefined,
    draftMessage: s.draftMessage ?? undefined,
    createdAt: new Date(s.createdAt),
    chatThread: apiMessagesToChatThread(s.messages ?? []),
  };
}

// --- API response interfaces ---

interface ApiHouseUnit {
  uid: string;
  label: string;
  isOccupied: boolean;
  context?: string;
}

interface ApiHouse {
  uid: string;
  name: string;
  address: string;
  propertyType?: string;
  source?: string;
  context?: string;
  units?: number;
  occupiedUnits?: number;
  monthlyRevenue?: number;
  unitList?: ApiHouseUnit[];
}

interface ApiVendor {
  uid: string;
  name: string;
  company?: string;
  vendorType?: string;
  phone?: string;
  email?: string;
  notes?: string;
  context?: string;
  portalUrl?: string;
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
  context?: string;
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
  suggestionId?: string;
  sentAt: string;
}

interface ApiTask {
  uid: string;
  taskNumber?: number | null;
  title?: string;
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
  requireVendorType?: string;
  assignedVendorId?: string;
  assignedVendorName?: string;
  steps?: { key: string; label: string; status: string; note?: string }[];
  suggestionOptions?: { key: string; label: string; action: string; variant: string }[];
  aiConversationId?: string | null;
  parentConversationId?: string | null;
  externalConversationId?: string | null;
}

interface ApiSuggestion {
  uid: string;
  title?: string;
  body?: string;
  category?: string;
  urgency?: string;
  status: string;
  source?: string;
  automationKey?: string;
  options?: { key: string; label: string; action: string; variant: string }[];
  actionTaken?: string;
  propertyId?: string;
  unitId?: string;
  taskId?: string;
  vendorName?: string;
  propertyName?: string;
  draftMessage?: string;
  createdAt: string;
  messages?: ApiTaskMessage[];
}
