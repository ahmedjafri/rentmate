import { print } from 'graphql';
import type { TypedDocumentNode } from '@graphql-typed-document-node/core';

import { getToken } from '@/lib/auth';
import {
  ActOnSuggestionDocument,
  AddLeaseForTenantDocument,
  AssignVendorToTaskDocument,
  ConversationMessagesDocument,
  CreatePropertyDocument,
  CreateScheduledTaskDocument,
  CreateTaskDocument,
  CreateTenantWithLeaseDocument,
  CreateVendorDocument,
  DeleteConversationDocument,
  DeletePropertyDocument,
  DeleteScheduledTaskDocument,
  DeleteTaskDocument,
  DeleteTenantDocument,
  DeleteVendorDocument,
  EntityNoteDocument,
  GetConversationsDocument,
  GetDocumentDocument,
  HousesDocument,
  LoginDocument,
  MeDocument,
  RunScheduledTaskDocument,
  SaveEntityNoteDocument,
  ScheduledTaskDocument,
  ScheduledTasksDocument,
  SendMessageDocument,
  SendSmsDocument,
  SuggestionsDocument,
  TaskDocument,
  TasksDocument,
  TenantsDocument,
  UpdateEntityContextDocument,
  UpdatePropertyDocument,
  UpdateScheduledTaskDocument,
  UpdateTaskDocument,
  UpdateTaskStatusDocument,
  UpdateVendorDocument,
  VendorTypesDocument,
  VendorsDocument,
  type ConversationType,
  type CreatePropertyInput,
  type CreateTaskInput,
  type CreateTenantWithLeaseInput,
  type CreateVendorInput,
  type LoginInput,
  type MessageType,
  type SuggestionStatus,
  type TaskCategory,
  type TaskMode,
  type TaskSource,
  type TaskStatus,
  type UpdatePropertyInput,
  type UpdateTaskInput,
  type UpdateVendorInput,
  type Urgency,
} from '@/graphql/generated';

const GRAPHQL_URL = '/graphql';

type GraphqlRequestOptions = {
  auth?: boolean;
};

function upperSnake(value: string): string {
  return value.trim().replace(/[^A-Za-z0-9]+/g, '_').toUpperCase();
}

function maybeEnum<T extends string>(value: string | null | undefined): T | null | undefined {
  if (value == null) return value;
  return upperSnake(value) as T;
}

export function fromGraphqlEnum(value: string | null | undefined): string | undefined {
  return value == null ? undefined : value.toLowerCase();
}

export function fromGraphqlTaskStatus(value: string | null | undefined): 'active' | 'paused' | 'resolved' | 'cancelled' | undefined {
  if (value == null) return undefined;
  if (value === 'DISMISSED') return 'cancelled';
  return value.toLowerCase() as 'active' | 'paused' | 'resolved' | 'cancelled';
}

export function toGraphqlConversationType(value: string): ConversationType {
  return maybeEnum<ConversationType>(value)!;
}

export function toGraphqlMessageType(value: string): MessageType {
  return maybeEnum<MessageType>(value)!;
}

export function toGraphqlSuggestionStatus(value: string | null | undefined): SuggestionStatus | null | undefined {
  return maybeEnum<SuggestionStatus>(value);
}

export function toGraphqlTaskCategory(value: string | null | undefined): TaskCategory | null | undefined {
  return maybeEnum<TaskCategory>(value);
}

export function toGraphqlTaskMode(value: string | null | undefined): TaskMode | null | undefined {
  return maybeEnum<TaskMode>(value);
}

export function toGraphqlTaskSource(value: string | null | undefined): TaskSource | null | undefined {
  return maybeEnum<TaskSource>(value);
}

export function toGraphqlTaskStatus(
  value: string | null | undefined,
): TaskStatus | null | undefined {
  if (value == null) return value;
  if (value === 'cancelled') return 'DISMISSED';
  return maybeEnum<TaskStatus>(value);
}

export function toGraphqlUrgency(value: string | null | undefined): Urgency | null | undefined {
  return maybeEnum<Urgency>(value);
}

function toIntId(value: string | number): number {
  const numeric = typeof value === 'number' ? value : Number(value);
  if (!Number.isInteger(numeric)) {
    throw new Error(`Expected integer id, got "${value}"`);
  }
  return numeric;
}

export async function graphqlRequest<TResult, TVariables>(
  document: TypedDocumentNode<TResult, TVariables>,
  variables: TVariables,
  options: GraphqlRequestOptions = {},
): Promise<TResult> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (options.auth !== false) {
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(GRAPHQL_URL, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      query: print(document),
      variables,
    }),
  });

  const text = await res.text();
  if (!text) throw new Error(`Server error (HTTP ${res.status})`);
  const { data, errors } = JSON.parse(text);
  if (errors?.length) {
    if (errors.some((e: { message: string }) => e.message.includes('Not authenticated'))) {
      localStorage.removeItem('jwtToken');
      window.dispatchEvent(new CustomEvent('auth:logout'));
      throw new Error('Session expired, please login.');
    }
    throw new Error(errors[0].message);
  }
  return data as TResult;
}

export function loginMutation(input: LoginInput) {
  return graphqlRequest(LoginDocument, { input }, { auth: false });
}

export function getMe() {
  return graphqlRequest(MeDocument, {});
}

export function listHouses() {
  return graphqlRequest(HousesDocument, {});
}

export function listTenants() {
  return graphqlRequest(TenantsDocument, {});
}

export function listTasks(filters: { category?: string | null; status?: Array<string> | null; source?: string | null } = {}) {
  return graphqlRequest(TasksDocument, {
    category: toGraphqlTaskCategory(filters.category),
    status: filters.status?.map((status) => toGraphqlTaskStatus(status)!),
    source: toGraphqlTaskSource(filters.source),
  });
}

export function getTask(uid: string | number) {
  return graphqlRequest(TaskDocument, { uid: toIntId(uid) });
}

export function listSuggestions(status?: string | null) {
  return graphqlRequest(SuggestionsDocument, {
    status: toGraphqlSuggestionStatus(status),
  });
}

export function actOnSuggestion(uid: string | number, action: string, editedBody?: string | null) {
  return graphqlRequest(ActOnSuggestionDocument, {
    uid: toIntId(uid),
    action,
    editedBody: editedBody ?? null,
  });
}

export function getConversationMessages(uid: string) {
  return graphqlRequest(ConversationMessagesDocument, { uid });
}

export function getConversations(conversationType: string, limit = 50) {
  return graphqlRequest(GetConversationsDocument, {
    conversationType: toGraphqlConversationType(conversationType),
    limit,
  });
}

export function deleteConversation(uid: string) {
  return graphqlRequest(DeleteConversationDocument, { uid });
}

export function sendMessage(input: {
  conversationId: string;
  body: string;
  messageType?: string;
  senderName?: string;
  isAi?: boolean;
}) {
  return graphqlRequest(SendMessageDocument, {
    input: {
      conversationId: input.conversationId,
      body: input.body,
      messageType: toGraphqlMessageType(input.messageType ?? 'message'),
      senderName: input.senderName ?? 'You',
      isAi: input.isAi ?? false,
    },
  });
}

export function createProperty(input: CreatePropertyInput) {
  return graphqlRequest(CreatePropertyDocument, { input });
}

export function updateProperty(input: UpdatePropertyInput) {
  return graphqlRequest(UpdatePropertyDocument, { input });
}

export function deleteProperty(uid: string) {
  return graphqlRequest(DeletePropertyDocument, { uid });
}

export function createTenantWithLease(input: CreateTenantWithLeaseInput) {
  return graphqlRequest(CreateTenantWithLeaseDocument, { input });
}

export function addLeaseForTenant(input: {
  tenantId: string;
  propertyId: string;
  unitId: string;
  leaseStart: string;
  leaseEnd: string;
  rentAmount: number;
}) {
  return graphqlRequest(AddLeaseForTenantDocument, { input });
}

export function deleteTenant(uid: string) {
  return graphqlRequest(DeleteTenantDocument, { uid });
}

export function getDocument(uid: string) {
  return graphqlRequest(GetDocumentDocument, { uid });
}

export function listVendors() {
  return graphqlRequest(VendorsDocument, {});
}

export function getVendorTypes() {
  return graphqlRequest(VendorTypesDocument, {});
}

export function createVendor(input: CreateVendorInput) {
  return graphqlRequest(CreateVendorDocument, { input });
}

export function updateVendor(input: UpdateVendorInput) {
  return graphqlRequest(UpdateVendorDocument, { input });
}

export function deleteVendor(uid: string) {
  return graphqlRequest(DeleteVendorDocument, { uid });
}

export function sendSms(vendorId: string, body: string, taskId?: string | number | null) {
  return graphqlRequest(SendSmsDocument, {
    vendorId,
    body,
    taskId: taskId == null ? null : toIntId(taskId),
  });
}

export function updateEntityContext(entityType: string, entityId: string, context: string) {
  return graphqlRequest(UpdateEntityContextDocument, { entityType, entityId, context });
}

export function getEntityNote(entityType: string, entityId: string) {
  return graphqlRequest(EntityNoteDocument, { entityType, entityId });
}

export function saveEntityNote(entityType: string, entityId: string, content: string) {
  return graphqlRequest(SaveEntityNoteDocument, { entityType, entityId, content });
}

export function createTask(input: Omit<CreateTaskInput, 'category' | 'source' | 'taskMode' | 'taskStatus' | 'urgency'> & {
  category?: string | null;
  source: string;
  taskMode?: string | null;
  taskStatus: string;
  urgency?: string | null;
}) {
  return graphqlRequest(CreateTaskDocument, {
    input: {
      ...input,
      category: toGraphqlTaskCategory(input.category),
      source: toGraphqlTaskSource(input.source)!,
      taskMode: toGraphqlTaskMode(input.taskMode),
      taskStatus: toGraphqlTaskStatus(input.taskStatus)!,
      urgency: toGraphqlUrgency(input.urgency),
    },
  });
}

export function updateTask(input: Omit<UpdateTaskInput, 'taskMode' | 'taskStatus' | 'uid'> & {
  uid: string | number;
  taskMode?: string | null;
  taskStatus?: string | null;
}) {
  return graphqlRequest(UpdateTaskDocument, {
    input: {
      uid: toIntId(input.uid),
      taskMode: toGraphqlTaskMode(input.taskMode),
      taskStatus: toGraphqlTaskStatus(input.taskStatus),
    },
  });
}

export function updateTaskStatus(uid: string | number, status: string) {
  return graphqlRequest(UpdateTaskStatusDocument, {
    uid: toIntId(uid),
    status: toGraphqlTaskStatus(status)!,
  });
}

export function deleteTask(uid: string | number) {
  return graphqlRequest(DeleteTaskDocument, { uid: toIntId(uid) });
}

export function assignVendorToTask(taskId: string | number, vendorId: string) {
  return graphqlRequest(AssignVendorToTaskDocument, {
    taskId: toIntId(taskId),
    vendorId,
  });
}

export function listScheduledTasks() {
  return graphqlRequest(ScheduledTasksDocument, {});
}

export function getScheduledTask(uid: string) {
  return graphqlRequest(ScheduledTaskDocument, { uid });
}

export function createScheduledTask(name: string, prompt: string, schedule: string, repeat?: number | null) {
  return graphqlRequest(CreateScheduledTaskDocument, {
    name,
    prompt,
    schedule,
    repeat: repeat ?? null,
  });
}

export function updateScheduledTask(uid: string, updates: {
  name?: string;
  prompt?: string;
  schedule?: string;
  enabled?: boolean;
}) {
  return graphqlRequest(UpdateScheduledTaskDocument, {
    uid,
    name: updates.name,
    prompt: updates.prompt,
    schedule: updates.schedule,
    enabled: updates.enabled,
  });
}

export function deleteScheduledTask(uid: string) {
  return graphqlRequest(DeleteScheduledTaskDocument, { uid });
}

export function runScheduledTask(uid: string) {
  return graphqlRequest(RunScheduledTaskDocument, { uid });
}
