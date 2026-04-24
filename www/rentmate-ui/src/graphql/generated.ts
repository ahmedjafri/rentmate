import { TypedDocumentNode as DocumentNode } from '@graphql-typed-document-node/core';
export type Maybe<T> = T | null;
export type InputMaybe<T> = Maybe<T>;
export type Exact<T extends { [key: string]: unknown }> = { [K in keyof T]: T[K] };
export type MakeOptional<T, K extends keyof T> = Omit<T, K> & { [SubKey in K]?: Maybe<T[SubKey]> };
export type MakeMaybe<T, K extends keyof T> = Omit<T, K> & { [SubKey in K]: Maybe<T[SubKey]> };
export type MakeEmpty<T extends { [key: string]: unknown }, K extends keyof T> = { [_ in K]?: never };
export type Incremental<T> = T | { [P in keyof T]?: P extends ' $fragmentName' | '__typename' ? T[P] : never };
/** All built-in and custom scalars, mapped to their actual values */
export type Scalars = {
  ID: { input: string; output: string; }
  String: { input: string; output: string; }
  Boolean: { input: boolean; output: boolean; }
  Int: { input: number; output: number; }
  Float: { input: number; output: number; }
  /** The `JSON` scalar type represents JSON values as specified by [ECMA-404](https://ecma-international.org/wp-content/uploads/ECMA-404_2nd_edition_december_2017.pdf). */
  JSON: { input: unknown; output: unknown; }
};

export type AddDocumentTagInput = {
  documentId: Scalars['String']['input'];
  propertyId: InputMaybe<Scalars['String']['input']>;
  tagType: Scalars['String']['input'];
  tenantId: InputMaybe<Scalars['String']['input']>;
  unitId: InputMaybe<Scalars['String']['input']>;
};

export type AddLeaseForTenantInput = {
  leaseEnd: Scalars['String']['input'];
  leaseStart: Scalars['String']['input'];
  propertyId: Scalars['String']['input'];
  rentAmount: Scalars['Float']['input'];
  tenantId: Scalars['String']['input'];
  unitId: Scalars['String']['input'];
};

export type AuthPayload = {
  token: Scalars['String']['output'];
  user: UserType;
};

export type ChatActionCardFieldType = {
  label: Scalars['String']['output'];
  value: Scalars['String']['output'];
};

export type ChatActionCardLinkType = {
  entityId: Scalars['String']['output'];
  entityType: Scalars['String']['output'];
  label: Scalars['String']['output'];
  propertyId: Maybe<Scalars['String']['output']>;
};

export type ChatActionCardType = {
  fields: Array<ChatActionCardFieldType>;
  kind: Scalars['String']['output'];
  links: Array<ChatActionCardLinkType>;
  summary: Maybe<Scalars['String']['output']>;
  title: Scalars['String']['output'];
  units: Array<ChatActionCardUnitType>;
};

export type ChatActionCardUnitType = {
  label: Scalars['String']['output'];
  propertyId: Scalars['String']['output'];
  uid: Scalars['String']['output'];
};

export type ChatMessageType = {
  actionCard: Maybe<ChatActionCardType>;
  approvalStatus: Maybe<Scalars['String']['output']>;
  body: Maybe<Scalars['String']['output']>;
  draftReply: Maybe<Scalars['String']['output']>;
  isAi: Scalars['Boolean']['output'];
  isSystem: Scalars['Boolean']['output'];
  messageType: Maybe<MessageType>;
  relatedTaskIds: Maybe<Scalars['JSON']['output']>;
  senderName: Maybe<Scalars['String']['output']>;
  senderType: Maybe<Scalars['String']['output']>;
  sentAt: Scalars['String']['output'];
  suggestionId: Maybe<Scalars['String']['output']>;
  uid: Scalars['String']['output'];
};

export type ConversationParticipantType = {
  entityId: Maybe<Scalars['String']['output']>;
  name: Scalars['String']['output'];
  participantType: Scalars['String']['output'];
  portalUrl: Maybe<Scalars['String']['output']>;
};

export type ConversationSummaryType = {
  conversationType: ConversationType;
  lastMessageAt: Maybe<Scalars['String']['output']>;
  lastMessageBody: Maybe<Scalars['String']['output']>;
  lastMessageSenderName: Maybe<Scalars['String']['output']>;
  participantCount: Scalars['Int']['output'];
  propertyName: Maybe<Scalars['String']['output']>;
  title: Maybe<Scalars['String']['output']>;
  uid: Scalars['String']['output'];
  unreadCount: Scalars['Int']['output'];
  updatedAt: Scalars['String']['output'];
};

export type ConversationType =
  | 'SUGGESTION_AI'
  | 'TASK_AI'
  | 'TENANT'
  | 'USER_AI'
  | 'VENDOR';

export type CreatePropertyInput = {
  address: Scalars['String']['input'];
  city: InputMaybe<Scalars['String']['input']>;
  name: InputMaybe<Scalars['String']['input']>;
  postalCode: InputMaybe<Scalars['String']['input']>;
  propertyType: Scalars['String']['input'];
  state: InputMaybe<Scalars['String']['input']>;
  unitLabels: InputMaybe<Array<Scalars['String']['input']>>;
};

export type CreateTaskInput = {
  category: InputMaybe<TaskCategory>;
  confidential: Scalars['Boolean']['input'];
  goal: Scalars['String']['input'];
  priority: InputMaybe<TaskPriority>;
  propertyId: InputMaybe<Scalars['String']['input']>;
  source: TaskSource;
  taskMode: InputMaybe<TaskMode>;
  taskStatus: TaskStatus;
  title: Scalars['String']['input'];
  unitId: InputMaybe<Scalars['String']['input']>;
  urgency: InputMaybe<Urgency>;
};

export type CreateTenantWithLeaseInput = {
  email: InputMaybe<Scalars['String']['input']>;
  firstName: Scalars['String']['input'];
  lastName: Scalars['String']['input'];
  leaseEnd: Scalars['String']['input'];
  leaseStart: Scalars['String']['input'];
  phone: InputMaybe<Scalars['String']['input']>;
  propertyId: Scalars['String']['input'];
  rentAmount: Scalars['Float']['input'];
  unitId: Scalars['String']['input'];
};

export type CreateVendorInput = {
  company: InputMaybe<Scalars['String']['input']>;
  email: InputMaybe<Scalars['String']['input']>;
  name: Scalars['String']['input'];
  notes: InputMaybe<Scalars['String']['input']>;
  phone: Scalars['String']['input'];
  vendorType: InputMaybe<Scalars['String']['input']>;
};

export type DocumentTagType = {
  createdAt: Scalars['String']['output'];
  documentId: Scalars['String']['output'];
  propertyId: Maybe<Scalars['String']['output']>;
  tagType: Scalars['String']['output'];
  tenantId: Maybe<Scalars['String']['output']>;
  uid: Scalars['String']['output'];
  unitId: Maybe<Scalars['String']['output']>;
};

export type DocumentType = {
  context: Maybe<Scalars['String']['output']>;
  createdAt: Maybe<Scalars['String']['output']>;
  documentType: Scalars['String']['output'];
  errorMessage: Maybe<Scalars['String']['output']>;
  extractedData: Maybe<Scalars['JSON']['output']>;
  extractionMeta: Maybe<Scalars['JSON']['output']>;
  filename: Scalars['String']['output'];
  processedAt: Maybe<Scalars['String']['output']>;
  progress: Maybe<Scalars['String']['output']>;
  rawText: Maybe<Scalars['String']['output']>;
  status: Scalars['String']['output'];
  uid: Scalars['String']['output'];
};

export type ExtraPropertyType = {
  key: Scalars['String']['output'];
  value: Scalars['String']['output'];
};

export type HouseType = {
  address: Scalars['String']['output'];
  context: Maybe<Scalars['String']['output']>;
  leases: Array<LeaseType>;
  monthlyRevenue: Maybe<Scalars['Float']['output']>;
  name: Scalars['String']['output'];
  occupiedUnits: Maybe<Scalars['Int']['output']>;
  propertyType: Maybe<Scalars['String']['output']>;
  source: Maybe<Scalars['String']['output']>;
  tenants: Array<TenantType>;
  uid: Scalars['String']['output'];
  unitList: Array<UnitType>;
  units: Maybe<Scalars['Int']['output']>;
};

export type LeaseType = {
  endDate: Scalars['String']['output'];
  house: Maybe<HouseType>;
  rentAmount: Scalars['Float']['output'];
  startDate: Scalars['String']['output'];
  tenant: Maybe<TenantType>;
  uid: Scalars['String']['output'];
};

export type LinkedConversationType = {
  conversationType: ConversationType;
  label: Scalars['String']['output'];
  lastMessageAt: Maybe<Scalars['String']['output']>;
  messageCount: Scalars['Int']['output'];
  participants: Array<ConversationParticipantType>;
  uid: Scalars['String']['output'];
};

export type LoginInput = {
  email: InputMaybe<Scalars['String']['input']>;
  password: Scalars['String']['input'];
  username: InputMaybe<Scalars['String']['input']>;
};

export type MessageType =
  | 'ACTION'
  | 'APPROVAL'
  | 'CONTEXT'
  | 'DRAFT_AI_REPLY'
  | 'INTERNAL'
  | 'MESSAGE'
  | 'SUGGESTION'
  | 'THREAD';

export type Mutation = {
  /** Act on a suggestion (accept, reject, approve draft, etc.) */
  actOnSuggestion: SuggestionType;
  /** Tag a document to a property, unit, or tenant */
  addDocumentTag: DocumentTagType;
  /** Attach a new lease to an existing tenant on an existing unit */
  addLeaseForTenant: TenantType;
  /** Assign a vendor to a task */
  assignVendorToTask: TaskType;
  /** Set confirmed_at on a document */
  confirmDocument: Scalars['Boolean']['output'];
  /** Manually create a property with optional units */
  createProperty: HouseType;
  /** Create a routine */
  createRoutine: RoutineType;
  /** Create a new task */
  createTask: TaskType;
  /** Create a tenant and attach a lease to an existing unit */
  createTenantWithLease: TenantType;
  /** Create a new vendor contact */
  createVendor: VendorType;
  /** Archive (soft-delete) a conversation */
  deleteConversation: Scalars['Boolean']['output'];
  /** Delete a property and all its units/leases (cascade) */
  deleteProperty: Scalars['Boolean']['output'];
  /** Delete a routine */
  deleteRoutine: Scalars['Boolean']['output'];
  /** Permanently delete a task and all its messages */
  deleteTask: Scalars['Boolean']['output'];
  /** Delete a tenant and all their leases (cascade) */
  deleteTenant: Scalars['Boolean']['output'];
  /** Delete a vendor contact */
  deleteVendor: Scalars['Boolean']['output'];
  login: AuthPayload;
  /** Run a routine immediately and return its output */
  runRoutine: RoutineType;
  /** Save private (per-account) notes for an entity */
  saveEntityNote: Scalars['Boolean']['output'];
  /** Add a message to any conversation */
  sendMessage: ChatMessageType;
  /** Send a message to a vendor via their notification channel (SMS) */
  sendSms: ChatMessageType;
  /** Simulate a routine — dry run that returns what the agent would do */
  simulateRoutine: Scalars['String']['output'];
  /** Spawn a Task from an existing conversation, linking lineage */
  spawnTask: TaskType;
  /** Update the agent context for any entity (property, unit, tenant, vendor) */
  updateEntityContext: Scalars['Boolean']['output'];
  /** Update a property's name, address, or type */
  updateProperty: HouseType;
  /** Update a routine */
  updateRoutine: RoutineType;
  /** Update task mode and/or status */
  updateTask: TaskType;
  /** Set the manager-authored goal (intent) for a task */
  updateTaskGoal: TaskType;
  /** Transition task_status (e.g. suggested→active, active→resolved) */
  updateTaskStatus: TaskType;
  /** Update the ordered progress steps for a task */
  updateTaskSteps: TaskType;
  /** Update an existing vendor contact */
  updateVendor: VendorType;
};


export type MutationActOnSuggestionArgs = {
  action: Scalars['String']['input'];
  editedBody?: InputMaybe<Scalars['String']['input']>;
  uid: Scalars['Int']['input'];
};


export type MutationAddDocumentTagArgs = {
  input: AddDocumentTagInput;
};


export type MutationAddLeaseForTenantArgs = {
  input: AddLeaseForTenantInput;
};


export type MutationAssignVendorToTaskArgs = {
  taskId: Scalars['Int']['input'];
  vendorId: Scalars['String']['input'];
};


export type MutationConfirmDocumentArgs = {
  documentId: Scalars['String']['input'];
};


export type MutationCreatePropertyArgs = {
  input: CreatePropertyInput;
};


export type MutationCreateRoutineArgs = {
  name: Scalars['String']['input'];
  prompt: Scalars['String']['input'];
  repeat?: InputMaybe<Scalars['Int']['input']>;
  schedule: Scalars['String']['input'];
};


export type MutationCreateTaskArgs = {
  input: CreateTaskInput;
};


export type MutationCreateTenantWithLeaseArgs = {
  input: CreateTenantWithLeaseInput;
};


export type MutationCreateVendorArgs = {
  input: CreateVendorInput;
};


export type MutationDeleteConversationArgs = {
  uid: Scalars['String']['input'];
};


export type MutationDeletePropertyArgs = {
  uid: Scalars['String']['input'];
};


export type MutationDeleteRoutineArgs = {
  uid: Scalars['Int']['input'];
};


export type MutationDeleteTaskArgs = {
  uid: Scalars['Int']['input'];
};


export type MutationDeleteTenantArgs = {
  uid: Scalars['String']['input'];
};


export type MutationDeleteVendorArgs = {
  uid: Scalars['String']['input'];
};


export type MutationLoginArgs = {
  input: LoginInput;
};


export type MutationRunRoutineArgs = {
  uid: Scalars['Int']['input'];
};


export type MutationSaveEntityNoteArgs = {
  content: Scalars['String']['input'];
  entityId: Scalars['String']['input'];
  entityType: Scalars['String']['input'];
};


export type MutationSendMessageArgs = {
  input: SendMessageInput;
};


export type MutationSendSmsArgs = {
  body: Scalars['String']['input'];
  taskId?: InputMaybe<Scalars['Int']['input']>;
  vendorId: Scalars['String']['input'];
};


export type MutationSimulateRoutineArgs = {
  uid: Scalars['Int']['input'];
};


export type MutationSpawnTaskArgs = {
  input: SpawnTaskInput;
};


export type MutationUpdateEntityContextArgs = {
  context: Scalars['String']['input'];
  entityId: Scalars['String']['input'];
  entityType: Scalars['String']['input'];
};


export type MutationUpdatePropertyArgs = {
  input: UpdatePropertyInput;
};


export type MutationUpdateRoutineArgs = {
  enabled?: InputMaybe<Scalars['Boolean']['input']>;
  name?: InputMaybe<Scalars['String']['input']>;
  prompt?: InputMaybe<Scalars['String']['input']>;
  schedule?: InputMaybe<Scalars['String']['input']>;
  uid: Scalars['Int']['input'];
};


export type MutationUpdateTaskArgs = {
  input: UpdateTaskInput;
};


export type MutationUpdateTaskGoalArgs = {
  goal: Scalars['String']['input'];
  uid: Scalars['Int']['input'];
};


export type MutationUpdateTaskStatusArgs = {
  status: TaskStatus;
  uid: Scalars['Int']['input'];
};


export type MutationUpdateTaskStepsArgs = {
  steps: Scalars['JSON']['input'];
  uid: Scalars['Int']['input'];
};


export type MutationUpdateVendorArgs = {
  input: UpdateVendorInput;
};

export type Query = {
  /** Returns messages for a conversation by uid */
  conversationMessages: Array<ChatMessageType>;
  /** Returns conversations by type (tenant/vendor/user_ai/task_ai) */
  conversations: Array<ConversationSummaryType>;
  /** Returns a single document by ID */
  document: Maybe<DocumentType>;
  /** Get private (per-account) notes for an entity */
  entityNote: Maybe<Scalars['String']['output']>;
  /** Returns all properties with their tenants and leases */
  houses: Array<HouseType>;
  /** Returns all leases with nested tenant and property */
  leases: Array<LeaseType>;
  /** Return the current authenticated user */
  me: UserType;
  /** Returns a single routine by ID */
  routine: Maybe<RoutineType>;
  /** Returns all routines */
  routines: Array<RoutineType>;
  /** Returns suggestions, optionally filtered by status and/or document */
  suggestions: Array<SuggestionType>;
  /** Returns a single task by uid, including its full message thread */
  task: Maybe<TaskType>;
  /** Returns tasks. Filters: category, status list, source */
  tasks: Array<TaskType>;
  /** Returns all tenants with their leases and properties */
  tenants: Array<TenantType>;
  /** Returns the list of valid vendor type labels */
  vendorTypes: Array<Scalars['String']['output']>;
  /** Returns all vendors */
  vendors: Array<VendorType>;
};


export type QueryConversationMessagesArgs = {
  uid: Scalars['String']['input'];
};


export type QueryConversationsArgs = {
  conversationType: ConversationType;
  limit?: Scalars['Int']['input'];
  offset?: Scalars['Int']['input'];
};


export type QueryDocumentArgs = {
  uid: Scalars['String']['input'];
};


export type QueryEntityNoteArgs = {
  entityId: Scalars['String']['input'];
  entityType: Scalars['String']['input'];
};


export type QueryRoutineArgs = {
  uid: Scalars['Int']['input'];
};


export type QueryRoutinesArgs = {
  enabled?: InputMaybe<Scalars['Boolean']['input']>;
};


export type QuerySuggestionsArgs = {
  documentId?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
  status?: InputMaybe<SuggestionStatus>;
};


export type QueryTaskArgs = {
  uid: Scalars['Int']['input'];
};


export type QueryTasksArgs = {
  category?: InputMaybe<TaskCategory>;
  source?: InputMaybe<TaskSource>;
  status?: InputMaybe<Array<TaskStatus>>;
};

export type RoutineState =
  | 'COMPLETED'
  | 'PAUSED'
  | 'SCHEDULED';

export type RoutineType = {
  completedCount: Scalars['Int']['output'];
  createdAt: Scalars['String']['output'];
  enabled: Scalars['Boolean']['output'];
  isDefault: Scalars['Boolean']['output'];
  lastOutput: Maybe<Scalars['String']['output']>;
  lastRunAt: Maybe<Scalars['String']['output']>;
  lastStatus: Maybe<Scalars['String']['output']>;
  name: Scalars['String']['output'];
  nextRunAt: Maybe<Scalars['String']['output']>;
  prompt: Scalars['String']['output'];
  repeat: Maybe<Scalars['Int']['output']>;
  schedule: Scalars['String']['output'];
  scheduleDisplay: Maybe<Scalars['String']['output']>;
  simulatedAt: Maybe<Scalars['String']['output']>;
  state: RoutineState;
  uid: Scalars['Int']['output'];
};

export type SendMessageInput = {
  body: Scalars['String']['input'];
  conversationId: Scalars['String']['input'];
  isAi: Scalars['Boolean']['input'];
  messageType: MessageType;
  senderName: Scalars['String']['input'];
};

export type SpawnTaskInput = {
  category: InputMaybe<TaskCategory>;
  objective: Scalars['String']['input'];
  parentConversationId: Scalars['String']['input'];
  priority: InputMaybe<TaskPriority>;
  source: TaskSource;
  taskMode: TaskMode;
  urgency: InputMaybe<Urgency>;
};

export type SuggestionSource =
  | 'AGENT'
  | 'AUTOMATION';

export type SuggestionStatus =
  | 'ACCEPTED'
  | 'DISMISSED'
  | 'EXPIRED'
  | 'PENDING';

export type SuggestionType = {
  actionTaken: Maybe<Scalars['String']['output']>;
  automationKey: Maybe<Scalars['String']['output']>;
  body: Maybe<Scalars['String']['output']>;
  category: Maybe<TaskCategory>;
  createdAt: Scalars['String']['output'];
  draftMessage: Maybe<Scalars['String']['output']>;
  messages: Array<ChatMessageType>;
  options: Maybe<Scalars['JSON']['output']>;
  propertyId: Maybe<Scalars['String']['output']>;
  propertyName: Maybe<Scalars['String']['output']>;
  riskScore: Maybe<Scalars['Int']['output']>;
  source: Maybe<SuggestionSource>;
  status: SuggestionStatus;
  suggestionType: Maybe<Scalars['String']['output']>;
  taskId: Maybe<Scalars['String']['output']>;
  title: Maybe<Scalars['String']['output']>;
  uid: Scalars['Int']['output'];
  unitId: Maybe<Scalars['String']['output']>;
  urgency: Maybe<Urgency>;
  vendorName: Maybe<Scalars['String']['output']>;
};

export type TaskCategory =
  | 'COMPLIANCE'
  | 'LEASING'
  | 'MAINTENANCE'
  | 'OTHER'
  | 'RENT';

export type TaskMode =
  | 'AUTONOMOUS'
  | 'MANUAL'
  | 'WAITING_APPROVAL';

export type TaskPriority =
  | 'ROUTINE'
  | 'URGENT';

export type TaskSource =
  | 'AGENT'
  | 'AI_SUGGESTION'
  | 'AUTOMATION'
  | 'DEV_SIM'
  | 'DOCUMENT'
  | 'MANUAL'
  | 'TENANT_REPORT';

export type TaskStatus =
  | 'ACTIVE'
  | 'DISMISSED'
  | 'PAUSED'
  | 'RESOLVED'
  | 'SUGGESTED';

export type TaskType = {
  aiConversationId: Maybe<Scalars['String']['output']>;
  aiTriageSuggestion: Maybe<Scalars['String']['output']>;
  ancestorIds: Maybe<Array<Scalars['String']['output']>>;
  assignedVendorId: Maybe<Scalars['String']['output']>;
  assignedVendorName: Maybe<Scalars['String']['output']>;
  category: Maybe<TaskCategory>;
  confidential: Scalars['Boolean']['output'];
  createdAt: Scalars['String']['output'];
  /** @deprecated Use externalConversationIds instead. */
  externalConversationId: Maybe<Scalars['String']['output']>;
  externalConversationIds: Array<Scalars['String']['output']>;
  goal: Maybe<Scalars['String']['output']>;
  lastMessageAt: Maybe<Scalars['String']['output']>;
  lastReviewNextStep: Maybe<Scalars['String']['output']>;
  lastReviewStatus: Maybe<Scalars['String']['output']>;
  lastReviewSummary: Maybe<Scalars['String']['output']>;
  lastReviewedAt: Maybe<Scalars['String']['output']>;
  linkedConversations: Array<LinkedConversationType>;
  messages: Array<ChatMessageType>;
  parentConversationId: Maybe<Scalars['String']['output']>;
  priority: Maybe<TaskPriority>;
  propertyId: Maybe<Scalars['String']['output']>;
  requireVendorType: Maybe<Scalars['String']['output']>;
  source: Maybe<TaskSource>;
  steps: Maybe<Scalars['JSON']['output']>;
  suggestionOptions: Maybe<Scalars['JSON']['output']>;
  taskMode: Maybe<TaskMode>;
  taskNumber: Maybe<Scalars['Int']['output']>;
  taskStatus: Maybe<TaskStatus>;
  tenantName: Maybe<Scalars['String']['output']>;
  title: Maybe<Scalars['String']['output']>;
  uid: Scalars['Int']['output'];
  unitId: Maybe<Scalars['String']['output']>;
  unitLabel: Maybe<Scalars['String']['output']>;
  urgency: Maybe<Urgency>;
  vendorAssigned: Maybe<Scalars['String']['output']>;
};

export type TenantType = {
  context: Maybe<Scalars['String']['output']>;
  email: Maybe<Scalars['String']['output']>;
  extraProperties: Array<ExtraPropertyType>;
  isActive: Scalars['Boolean']['output'];
  leaseEndDate: Maybe<Scalars['String']['output']>;
  leases: Array<LeaseType>;
  name: Scalars['String']['output'];
  paymentStatus: Maybe<Scalars['String']['output']>;
  portalUrl: Maybe<Scalars['String']['output']>;
  rentAmount: Maybe<Scalars['Float']['output']>;
  rents: Array<HouseType>;
  uid: Scalars['String']['output'];
  unitLabel: Maybe<Scalars['String']['output']>;
};

export type UnitType = {
  context: Maybe<Scalars['String']['output']>;
  isOccupied: Scalars['Boolean']['output'];
  label: Scalars['String']['output'];
  uid: Scalars['String']['output'];
};

export type UpdatePropertyInput = {
  address: InputMaybe<Scalars['String']['input']>;
  name: InputMaybe<Scalars['String']['input']>;
  propertyType: InputMaybe<Scalars['String']['input']>;
  uid: Scalars['String']['input'];
};

export type UpdateTaskInput = {
  category: InputMaybe<TaskCategory>;
  taskMode: InputMaybe<TaskMode>;
  taskStatus: InputMaybe<TaskStatus>;
  uid: Scalars['Int']['input'];
  urgency: InputMaybe<Urgency>;
};

export type UpdateVendorInput = {
  company: InputMaybe<Scalars['String']['input']>;
  email: InputMaybe<Scalars['String']['input']>;
  name: InputMaybe<Scalars['String']['input']>;
  notes: InputMaybe<Scalars['String']['input']>;
  phone: InputMaybe<Scalars['String']['input']>;
  uid: Scalars['String']['input'];
  vendorType: InputMaybe<Scalars['String']['input']>;
};

export type Urgency =
  | 'CRITICAL'
  | 'HIGH'
  | 'LOW'
  | 'MEDIUM';

export type UserType = {
  role: Maybe<Scalars['String']['output']>;
  uid: Scalars['String']['output'];
  username: Scalars['String']['output'];
};

export type VendorType = {
  company: Maybe<Scalars['String']['output']>;
  context: Maybe<Scalars['String']['output']>;
  createdAt: Scalars['String']['output'];
  email: Maybe<Scalars['String']['output']>;
  name: Scalars['String']['output'];
  notes: Maybe<Scalars['String']['output']>;
  phone: Maybe<Scalars['String']['output']>;
  portalUrl: Maybe<Scalars['String']['output']>;
  uid: Scalars['String']['output'];
  vendorType: Maybe<Scalars['String']['output']>;
};

export type TaskMessageFieldsFragment = { uid: string, body: string | null, messageType: MessageType | null, senderName: string | null, senderType: string | null, isAi: boolean, isSystem: boolean, draftReply: string | null, approvalStatus: string | null, relatedTaskIds: unknown | null, suggestionId: string | null, sentAt: string, actionCard: { kind: string, title: string, summary: string | null, fields: Array<{ label: string, value: string }>, links: Array<{ label: string, entityType: string, entityId: string, propertyId: string | null }>, units: Array<{ uid: string, label: string, propertyId: string }> } | null };

export type TaskFieldsFragment = { uid: number, taskNumber: number | null, title: string | null, taskStatus: TaskStatus | null, taskMode: TaskMode | null, source: TaskSource | null, category: TaskCategory | null, urgency: Urgency | null, priority: TaskPriority | null, confidential: boolean, lastMessageAt: string | null, propertyId: string | null, unitId: string | null, createdAt: string, tenantName: string | null, unitLabel: string | null, aiTriageSuggestion: string | null, vendorAssigned: string | null, requireVendorType: string | null, assignedVendorId: string | null, assignedVendorName: string | null, aiConversationId: string | null, parentConversationId: string | null, externalConversationIds: Array<string>, steps: unknown | null, goal: string | null, suggestionOptions: unknown | null, lastReviewedAt: string | null, lastReviewStatus: string | null, lastReviewSummary: string | null, lastReviewNextStep: string | null, linkedConversations: Array<{ uid: string, label: string, conversationType: ConversationType, lastMessageAt: string | null, messageCount: number, participants: Array<{ name: string, participantType: string, entityId: string | null, portalUrl: string | null }> }>, messages: Array<{ uid: string, body: string | null, messageType: MessageType | null, senderName: string | null, senderType: string | null, isAi: boolean, isSystem: boolean, draftReply: string | null, approvalStatus: string | null, relatedTaskIds: unknown | null, suggestionId: string | null, sentAt: string, actionCard: { kind: string, title: string, summary: string | null, fields: Array<{ label: string, value: string }>, links: Array<{ label: string, entityType: string, entityId: string, propertyId: string | null }>, units: Array<{ uid: string, label: string, propertyId: string }> } | null }> };

export type HousesQueryVariables = Exact<{ [key: string]: never; }>;


export type HousesQuery = { houses: Array<{ uid: string, name: string, address: string, propertyType: string | null, source: string | null, units: number | null, occupiedUnits: number | null, monthlyRevenue: number | null, context: string | null, unitList: Array<{ uid: string, label: string, isOccupied: boolean, context: string | null }>, tenants: Array<{ uid: string, name: string }>, leases: Array<{ uid: string, startDate: string, endDate: string, rentAmount: number, tenant: { uid: string, name: string } | null }> }> };

export type TenantsQueryVariables = Exact<{ [key: string]: never; }>;


export type TenantsQuery = { tenants: Array<{ uid: string, name: string, email: string | null, unitLabel: string | null, leaseEndDate: string | null, rentAmount: number | null, paymentStatus: string | null, isActive: boolean, context: string | null, portalUrl: string | null, extraProperties: Array<{ key: string, value: string }>, rents: Array<{ uid: string, name: string, address: string }>, leases: Array<{ uid: string, rentAmount: number, startDate: string, endDate: string }> }> };

export type TasksQueryVariables = Exact<{
  category: InputMaybe<TaskCategory>;
  status: InputMaybe<Array<TaskStatus> | TaskStatus>;
  source: InputMaybe<TaskSource>;
}>;


export type TasksQuery = { tasks: Array<{ uid: number, taskNumber: number | null, title: string | null, taskStatus: TaskStatus | null, taskMode: TaskMode | null, source: TaskSource | null, category: TaskCategory | null, urgency: Urgency | null, priority: TaskPriority | null, confidential: boolean, lastMessageAt: string | null, propertyId: string | null, unitId: string | null, createdAt: string, tenantName: string | null, unitLabel: string | null, aiTriageSuggestion: string | null, vendorAssigned: string | null, requireVendorType: string | null, assignedVendorId: string | null, assignedVendorName: string | null, aiConversationId: string | null, parentConversationId: string | null, externalConversationIds: Array<string>, steps: unknown | null, goal: string | null, suggestionOptions: unknown | null, lastReviewedAt: string | null, lastReviewStatus: string | null, lastReviewSummary: string | null, lastReviewNextStep: string | null, linkedConversations: Array<{ uid: string, label: string, conversationType: ConversationType, lastMessageAt: string | null, messageCount: number, participants: Array<{ name: string, participantType: string, entityId: string | null, portalUrl: string | null }> }>, messages: Array<{ uid: string, body: string | null, messageType: MessageType | null, senderName: string | null, senderType: string | null, isAi: boolean, isSystem: boolean, draftReply: string | null, approvalStatus: string | null, relatedTaskIds: unknown | null, suggestionId: string | null, sentAt: string, actionCard: { kind: string, title: string, summary: string | null, fields: Array<{ label: string, value: string }>, links: Array<{ label: string, entityType: string, entityId: string, propertyId: string | null }>, units: Array<{ uid: string, label: string, propertyId: string }> } | null }> }> };

export type TaskQueryVariables = Exact<{
  uid: Scalars['Int']['input'];
}>;


export type TaskQuery = { task: { uid: number, taskNumber: number | null, title: string | null, taskStatus: TaskStatus | null, taskMode: TaskMode | null, source: TaskSource | null, category: TaskCategory | null, urgency: Urgency | null, priority: TaskPriority | null, confidential: boolean, lastMessageAt: string | null, propertyId: string | null, unitId: string | null, createdAt: string, tenantName: string | null, unitLabel: string | null, aiTriageSuggestion: string | null, vendorAssigned: string | null, requireVendorType: string | null, assignedVendorId: string | null, assignedVendorName: string | null, aiConversationId: string | null, parentConversationId: string | null, externalConversationIds: Array<string>, steps: unknown | null, goal: string | null, suggestionOptions: unknown | null, lastReviewedAt: string | null, lastReviewStatus: string | null, lastReviewSummary: string | null, lastReviewNextStep: string | null, linkedConversations: Array<{ uid: string, label: string, conversationType: ConversationType, lastMessageAt: string | null, messageCount: number, participants: Array<{ name: string, participantType: string, entityId: string | null, portalUrl: string | null }> }>, messages: Array<{ uid: string, body: string | null, messageType: MessageType | null, senderName: string | null, senderType: string | null, isAi: boolean, isSystem: boolean, draftReply: string | null, approvalStatus: string | null, relatedTaskIds: unknown | null, suggestionId: string | null, sentAt: string, actionCard: { kind: string, title: string, summary: string | null, fields: Array<{ label: string, value: string }>, links: Array<{ label: string, entityType: string, entityId: string, propertyId: string | null }>, units: Array<{ uid: string, label: string, propertyId: string }> } | null }> } | null };

export type SuggestionsQueryVariables = Exact<{
  status: InputMaybe<SuggestionStatus>;
}>;


export type SuggestionsQuery = { suggestions: Array<{ uid: number, title: string | null, body: string | null, category: TaskCategory | null, urgency: Urgency | null, status: SuggestionStatus, source: SuggestionSource | null, automationKey: string | null, options: unknown | null, actionTaken: string | null, propertyId: string | null, unitId: string | null, taskId: string | null, vendorName: string | null, propertyName: string | null, draftMessage: string | null, createdAt: string, messages: Array<{ uid: string, body: string | null, messageType: MessageType | null, senderName: string | null, isAi: boolean, isSystem: boolean, sentAt: string, draftReply: string | null, approvalStatus: string | null }> }> };

export type ConversationMessagesQueryVariables = Exact<{
  uid: Scalars['String']['input'];
}>;


export type ConversationMessagesQuery = { conversationMessages: Array<{ uid: string, body: string | null, messageType: MessageType | null, senderName: string | null, senderType: string | null, isAi: boolean, isSystem: boolean, draftReply: string | null, approvalStatus: string | null, relatedTaskIds: unknown | null, suggestionId: string | null, sentAt: string, actionCard: { kind: string, title: string, summary: string | null, fields: Array<{ label: string, value: string }>, links: Array<{ label: string, entityType: string, entityId: string, propertyId: string | null }>, units: Array<{ uid: string, label: string, propertyId: string }> } | null }> };

export type VendorsQueryVariables = Exact<{ [key: string]: never; }>;


export type VendorsQuery = { vendors: Array<{ uid: string, name: string, company: string | null, vendorType: string | null, phone: string | null, email: string | null, notes: string | null, context: string | null, portalUrl: string | null, createdAt: string }> };

export type VendorTypesQueryVariables = Exact<{ [key: string]: never; }>;


export type VendorTypesQuery = { vendorTypes: Array<string> };

export type EntityNoteQueryVariables = Exact<{
  entityType: Scalars['String']['input'];
  entityId: Scalars['String']['input'];
}>;


export type EntityNoteQuery = { entityNote: string | null };

export type GetDocumentQueryVariables = Exact<{
  uid: Scalars['String']['input'];
}>;


export type GetDocumentQuery = { document: { uid: string, filename: string, documentType: string, status: string, progress: string | null, extractedData: unknown | null, extractionMeta: unknown | null, context: string | null, rawText: string | null, errorMessage: string | null, createdAt: string | null, processedAt: string | null } | null };

export type GetConversationsQueryVariables = Exact<{
  conversationType: ConversationType;
  limit: InputMaybe<Scalars['Int']['input']>;
}>;


export type GetConversationsQuery = { conversations: Array<{ uid: string, conversationType: ConversationType, title: string | null, lastMessageAt: string | null, updatedAt: string, lastMessageBody: string | null, lastMessageSenderName: string | null, propertyName: string | null, participantCount: number, unreadCount: number }> };

export type RoutinesQueryVariables = Exact<{ [key: string]: never; }>;


export type RoutinesQuery = { routines: Array<{ uid: number, name: string, prompt: string, schedule: string, scheduleDisplay: string | null, enabled: boolean, state: RoutineState, repeat: number | null, completedCount: number, nextRunAt: string | null, lastRunAt: string | null, lastStatus: string | null, lastOutput: string | null, createdAt: string }> };

export type RoutineQueryVariables = Exact<{
  uid: Scalars['Int']['input'];
}>;


export type RoutineQuery = { routine: { uid: number, name: string, prompt: string, schedule: string, scheduleDisplay: string | null, isDefault: boolean, enabled: boolean, state: RoutineState, repeat: number | null, completedCount: number, nextRunAt: string | null, lastRunAt: string | null, lastStatus: string | null, lastOutput: string | null, simulatedAt: string | null, createdAt: string } | null };

export type MeQueryVariables = Exact<{ [key: string]: never; }>;


export type MeQuery = { me: { uid: string, username: string } };

export type LoginMutationVariables = Exact<{
  input: LoginInput;
}>;


export type LoginMutation = { login: { token: string, user: { uid: string, username: string } } };

export type CreatePropertyMutationVariables = Exact<{
  input: CreatePropertyInput;
}>;


export type CreatePropertyMutation = { createProperty: { uid: string, name: string, address: string, propertyType: string | null, source: string | null, units: number | null, occupiedUnits: number | null, monthlyRevenue: number | null, unitList: Array<{ uid: string, label: string, isOccupied: boolean }> } };

export type UpdatePropertyMutationVariables = Exact<{
  input: UpdatePropertyInput;
}>;


export type UpdatePropertyMutation = { updateProperty: { uid: string, name: string, address: string, propertyType: string | null } };

export type DeletePropertyMutationVariables = Exact<{
  uid: Scalars['String']['input'];
}>;


export type DeletePropertyMutation = { deleteProperty: boolean };

export type AddLeaseForTenantMutationVariables = Exact<{
  input: AddLeaseForTenantInput;
}>;


export type AddLeaseForTenantMutation = { addLeaseForTenant: { uid: string, name: string, email: string | null, unitLabel: string | null, leaseEndDate: string | null, rentAmount: number | null, paymentStatus: string | null, isActive: boolean } };

export type CreateTenantWithLeaseMutationVariables = Exact<{
  input: CreateTenantWithLeaseInput;
}>;


export type CreateTenantWithLeaseMutation = { createTenantWithLease: { uid: string, name: string, email: string | null, unitLabel: string | null, leaseEndDate: string | null, rentAmount: number | null, paymentStatus: string | null, isActive: boolean } };

export type DeleteTenantMutationVariables = Exact<{
  uid: Scalars['String']['input'];
}>;


export type DeleteTenantMutation = { deleteTenant: boolean };

export type CreateTaskMutationVariables = Exact<{
  input: CreateTaskInput;
}>;


export type CreateTaskMutation = { createTask: { uid: number, title: string | null, taskStatus: TaskStatus | null, taskMode: TaskMode | null, category: TaskCategory | null, urgency: Urgency | null, source: TaskSource | null, propertyId: string | null, unitId: string | null, aiConversationId: string | null, createdAt: string } };

export type UpdateTaskMutationVariables = Exact<{
  input: UpdateTaskInput;
}>;


export type UpdateTaskMutation = { updateTask: { uid: number, category: TaskCategory | null, taskStatus: TaskStatus | null, taskMode: TaskMode | null, urgency: Urgency | null } };

export type UpdateTaskStatusMutationVariables = Exact<{
  uid: Scalars['Int']['input'];
  status: TaskStatus;
}>;


export type UpdateTaskStatusMutation = { updateTaskStatus: { uid: number, taskStatus: TaskStatus | null } };

export type UpdateTaskGoalMutationVariables = Exact<{
  uid: Scalars['Int']['input'];
  goal: Scalars['String']['input'];
}>;


export type UpdateTaskGoalMutation = { updateTaskGoal: { uid: number, goal: string | null } };

export type DeleteTaskMutationVariables = Exact<{
  uid: Scalars['Int']['input'];
}>;


export type DeleteTaskMutation = { deleteTask: boolean };

export type AssignVendorToTaskMutationVariables = Exact<{
  taskId: Scalars['Int']['input'];
  vendorId: Scalars['String']['input'];
}>;


export type AssignVendorToTaskMutation = { assignVendorToTask: { uid: number, assignedVendorId: string | null, assignedVendorName: string | null } };

export type ActOnSuggestionMutationVariables = Exact<{
  uid: Scalars['Int']['input'];
  action: Scalars['String']['input'];
  editedBody: InputMaybe<Scalars['String']['input']>;
}>;


export type ActOnSuggestionMutation = { actOnSuggestion: { uid: number, status: SuggestionStatus, actionTaken: string | null, taskId: string | null } };

export type SendMessageMutationVariables = Exact<{
  input: SendMessageInput;
}>;


export type SendMessageMutation = { sendMessage: { uid: string, body: string | null, messageType: MessageType | null, senderName: string | null, senderType: string | null, isAi: boolean, isSystem: boolean, draftReply: string | null, approvalStatus: string | null, suggestionId: string | null, sentAt: string, actionCard: { kind: string, title: string, summary: string | null, fields: Array<{ label: string, value: string }>, links: Array<{ label: string, entityType: string, entityId: string, propertyId: string | null }>, units: Array<{ uid: string, label: string, propertyId: string }> } | null } };

export type SendSmsMutationVariables = Exact<{
  vendorId: Scalars['String']['input'];
  body: Scalars['String']['input'];
  taskId: InputMaybe<Scalars['Int']['input']>;
}>;


export type SendSmsMutation = { sendSms: { uid: string, body: string | null, senderName: string | null, sentAt: string } };

export type DeleteConversationMutationVariables = Exact<{
  uid: Scalars['String']['input'];
}>;


export type DeleteConversationMutation = { deleteConversation: boolean };

export type CreateVendorMutationVariables = Exact<{
  input: CreateVendorInput;
}>;


export type CreateVendorMutation = { createVendor: { uid: string, name: string, company: string | null, vendorType: string | null, phone: string | null, email: string | null, notes: string | null, portalUrl: string | null } };

export type UpdateVendorMutationVariables = Exact<{
  input: UpdateVendorInput;
}>;


export type UpdateVendorMutation = { updateVendor: { uid: string, name: string, company: string | null, vendorType: string | null, phone: string | null, email: string | null, notes: string | null, portalUrl: string | null } };

export type DeleteVendorMutationVariables = Exact<{
  uid: Scalars['String']['input'];
}>;


export type DeleteVendorMutation = { deleteVendor: boolean };

export type UpdateEntityContextMutationVariables = Exact<{
  entityType: Scalars['String']['input'];
  entityId: Scalars['String']['input'];
  context: Scalars['String']['input'];
}>;


export type UpdateEntityContextMutation = { updateEntityContext: boolean };

export type SaveEntityNoteMutationVariables = Exact<{
  entityType: Scalars['String']['input'];
  entityId: Scalars['String']['input'];
  content: Scalars['String']['input'];
}>;


export type SaveEntityNoteMutation = { saveEntityNote: boolean };

export type CreateRoutineMutationVariables = Exact<{
  name: Scalars['String']['input'];
  prompt: Scalars['String']['input'];
  schedule: Scalars['String']['input'];
  repeat: InputMaybe<Scalars['Int']['input']>;
}>;


export type CreateRoutineMutation = { createRoutine: { uid: number } };

export type UpdateRoutineMutationVariables = Exact<{
  uid: Scalars['Int']['input'];
  name: InputMaybe<Scalars['String']['input']>;
  prompt: InputMaybe<Scalars['String']['input']>;
  schedule: InputMaybe<Scalars['String']['input']>;
  enabled: InputMaybe<Scalars['Boolean']['input']>;
}>;


export type UpdateRoutineMutation = { updateRoutine: { uid: number } };

export type DeleteRoutineMutationVariables = Exact<{
  uid: Scalars['Int']['input'];
}>;


export type DeleteRoutineMutation = { deleteRoutine: boolean };

export type RunRoutineMutationVariables = Exact<{
  uid: Scalars['Int']['input'];
}>;


export type RunRoutineMutation = { runRoutine: { uid: number, lastStatus: string | null, lastOutput: string | null, lastRunAt: string | null } };

export const TaskMessageFieldsFragmentDoc = {"kind":"Document","definitions":[{"kind":"FragmentDefinition","name":{"kind":"Name","value":"TaskMessageFields"},"typeCondition":{"kind":"NamedType","name":{"kind":"Name","value":"ChatMessageType"}},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"body"}},{"kind":"Field","name":{"kind":"Name","value":"messageType"}},{"kind":"Field","name":{"kind":"Name","value":"senderName"}},{"kind":"Field","name":{"kind":"Name","value":"senderType"}},{"kind":"Field","name":{"kind":"Name","value":"isAi"}},{"kind":"Field","name":{"kind":"Name","value":"isSystem"}},{"kind":"Field","name":{"kind":"Name","value":"draftReply"}},{"kind":"Field","name":{"kind":"Name","value":"approvalStatus"}},{"kind":"Field","name":{"kind":"Name","value":"relatedTaskIds"}},{"kind":"Field","name":{"kind":"Name","value":"suggestionId"}},{"kind":"Field","name":{"kind":"Name","value":"actionCard"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"kind"}},{"kind":"Field","name":{"kind":"Name","value":"title"}},{"kind":"Field","name":{"kind":"Name","value":"summary"}},{"kind":"Field","name":{"kind":"Name","value":"fields"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"value"}}]}},{"kind":"Field","name":{"kind":"Name","value":"links"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"entityType"}},{"kind":"Field","name":{"kind":"Name","value":"entityId"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}}]}},{"kind":"Field","name":{"kind":"Name","value":"units"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}}]}}]}},{"kind":"Field","name":{"kind":"Name","value":"sentAt"}}]}}]} as unknown as DocumentNode<TaskMessageFieldsFragment, unknown>;
export const TaskFieldsFragmentDoc = {"kind":"Document","definitions":[{"kind":"FragmentDefinition","name":{"kind":"Name","value":"TaskFields"},"typeCondition":{"kind":"NamedType","name":{"kind":"Name","value":"TaskType"}},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"taskNumber"}},{"kind":"Field","name":{"kind":"Name","value":"title"}},{"kind":"Field","name":{"kind":"Name","value":"taskStatus"}},{"kind":"Field","name":{"kind":"Name","value":"taskMode"}},{"kind":"Field","name":{"kind":"Name","value":"source"}},{"kind":"Field","name":{"kind":"Name","value":"category"}},{"kind":"Field","name":{"kind":"Name","value":"urgency"}},{"kind":"Field","name":{"kind":"Name","value":"priority"}},{"kind":"Field","name":{"kind":"Name","value":"confidential"}},{"kind":"Field","name":{"kind":"Name","value":"lastMessageAt"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}},{"kind":"Field","name":{"kind":"Name","value":"unitId"}},{"kind":"Field","name":{"kind":"Name","value":"createdAt"}},{"kind":"Field","name":{"kind":"Name","value":"tenantName"}},{"kind":"Field","name":{"kind":"Name","value":"unitLabel"}},{"kind":"Field","name":{"kind":"Name","value":"aiTriageSuggestion"}},{"kind":"Field","name":{"kind":"Name","value":"vendorAssigned"}},{"kind":"Field","name":{"kind":"Name","value":"requireVendorType"}},{"kind":"Field","name":{"kind":"Name","value":"assignedVendorId"}},{"kind":"Field","name":{"kind":"Name","value":"assignedVendorName"}},{"kind":"Field","name":{"kind":"Name","value":"aiConversationId"}},{"kind":"Field","name":{"kind":"Name","value":"parentConversationId"}},{"kind":"Field","name":{"kind":"Name","value":"externalConversationIds"}},{"kind":"Field","name":{"kind":"Name","value":"steps"}},{"kind":"Field","name":{"kind":"Name","value":"goal"}},{"kind":"Field","name":{"kind":"Name","value":"suggestionOptions"}},{"kind":"Field","name":{"kind":"Name","value":"lastReviewedAt"}},{"kind":"Field","name":{"kind":"Name","value":"lastReviewStatus"}},{"kind":"Field","name":{"kind":"Name","value":"lastReviewSummary"}},{"kind":"Field","name":{"kind":"Name","value":"lastReviewNextStep"}},{"kind":"Field","name":{"kind":"Name","value":"linkedConversations"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"conversationType"}},{"kind":"Field","name":{"kind":"Name","value":"lastMessageAt"}},{"kind":"Field","name":{"kind":"Name","value":"messageCount"}},{"kind":"Field","name":{"kind":"Name","value":"participants"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"participantType"}},{"kind":"Field","name":{"kind":"Name","value":"entityId"}},{"kind":"Field","name":{"kind":"Name","value":"portalUrl"}}]}}]}},{"kind":"Field","name":{"kind":"Name","value":"messages"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"FragmentSpread","name":{"kind":"Name","value":"TaskMessageFields"}}]}}]}},{"kind":"FragmentDefinition","name":{"kind":"Name","value":"TaskMessageFields"},"typeCondition":{"kind":"NamedType","name":{"kind":"Name","value":"ChatMessageType"}},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"body"}},{"kind":"Field","name":{"kind":"Name","value":"messageType"}},{"kind":"Field","name":{"kind":"Name","value":"senderName"}},{"kind":"Field","name":{"kind":"Name","value":"senderType"}},{"kind":"Field","name":{"kind":"Name","value":"isAi"}},{"kind":"Field","name":{"kind":"Name","value":"isSystem"}},{"kind":"Field","name":{"kind":"Name","value":"draftReply"}},{"kind":"Field","name":{"kind":"Name","value":"approvalStatus"}},{"kind":"Field","name":{"kind":"Name","value":"relatedTaskIds"}},{"kind":"Field","name":{"kind":"Name","value":"suggestionId"}},{"kind":"Field","name":{"kind":"Name","value":"actionCard"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"kind"}},{"kind":"Field","name":{"kind":"Name","value":"title"}},{"kind":"Field","name":{"kind":"Name","value":"summary"}},{"kind":"Field","name":{"kind":"Name","value":"fields"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"value"}}]}},{"kind":"Field","name":{"kind":"Name","value":"links"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"entityType"}},{"kind":"Field","name":{"kind":"Name","value":"entityId"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}}]}},{"kind":"Field","name":{"kind":"Name","value":"units"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}}]}}]}},{"kind":"Field","name":{"kind":"Name","value":"sentAt"}}]}}]} as unknown as DocumentNode<TaskFieldsFragment, unknown>;
export const HousesDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"Houses"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"houses"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"address"}},{"kind":"Field","name":{"kind":"Name","value":"propertyType"}},{"kind":"Field","name":{"kind":"Name","value":"source"}},{"kind":"Field","name":{"kind":"Name","value":"units"}},{"kind":"Field","name":{"kind":"Name","value":"occupiedUnits"}},{"kind":"Field","name":{"kind":"Name","value":"monthlyRevenue"}},{"kind":"Field","name":{"kind":"Name","value":"context"}},{"kind":"Field","name":{"kind":"Name","value":"unitList"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"isOccupied"}},{"kind":"Field","name":{"kind":"Name","value":"context"}}]}},{"kind":"Field","name":{"kind":"Name","value":"tenants"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}}]}},{"kind":"Field","name":{"kind":"Name","value":"leases"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"startDate"}},{"kind":"Field","name":{"kind":"Name","value":"endDate"}},{"kind":"Field","name":{"kind":"Name","value":"rentAmount"}},{"kind":"Field","name":{"kind":"Name","value":"tenant"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}}]}}]}}]}}]}}]} as unknown as DocumentNode<HousesQuery, HousesQueryVariables>;
export const TenantsDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"Tenants"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"tenants"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"email"}},{"kind":"Field","name":{"kind":"Name","value":"unitLabel"}},{"kind":"Field","name":{"kind":"Name","value":"leaseEndDate"}},{"kind":"Field","name":{"kind":"Name","value":"rentAmount"}},{"kind":"Field","name":{"kind":"Name","value":"paymentStatus"}},{"kind":"Field","name":{"kind":"Name","value":"isActive"}},{"kind":"Field","name":{"kind":"Name","value":"context"}},{"kind":"Field","name":{"kind":"Name","value":"portalUrl"}},{"kind":"Field","name":{"kind":"Name","value":"extraProperties"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"key"}},{"kind":"Field","name":{"kind":"Name","value":"value"}}]}},{"kind":"Field","name":{"kind":"Name","value":"rents"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"address"}}]}},{"kind":"Field","name":{"kind":"Name","value":"leases"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"rentAmount"}},{"kind":"Field","name":{"kind":"Name","value":"startDate"}},{"kind":"Field","name":{"kind":"Name","value":"endDate"}}]}}]}}]}}]} as unknown as DocumentNode<TenantsQuery, TenantsQueryVariables>;
export const TasksDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"Tasks"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"category"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"TaskCategory"}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"status"}},"type":{"kind":"ListType","type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"TaskStatus"}}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"source"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"TaskSource"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"tasks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"category"},"value":{"kind":"Variable","name":{"kind":"Name","value":"category"}}},{"kind":"Argument","name":{"kind":"Name","value":"status"},"value":{"kind":"Variable","name":{"kind":"Name","value":"status"}}},{"kind":"Argument","name":{"kind":"Name","value":"source"},"value":{"kind":"Variable","name":{"kind":"Name","value":"source"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"FragmentSpread","name":{"kind":"Name","value":"TaskFields"}}]}}]}},{"kind":"FragmentDefinition","name":{"kind":"Name","value":"TaskMessageFields"},"typeCondition":{"kind":"NamedType","name":{"kind":"Name","value":"ChatMessageType"}},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"body"}},{"kind":"Field","name":{"kind":"Name","value":"messageType"}},{"kind":"Field","name":{"kind":"Name","value":"senderName"}},{"kind":"Field","name":{"kind":"Name","value":"senderType"}},{"kind":"Field","name":{"kind":"Name","value":"isAi"}},{"kind":"Field","name":{"kind":"Name","value":"isSystem"}},{"kind":"Field","name":{"kind":"Name","value":"draftReply"}},{"kind":"Field","name":{"kind":"Name","value":"approvalStatus"}},{"kind":"Field","name":{"kind":"Name","value":"relatedTaskIds"}},{"kind":"Field","name":{"kind":"Name","value":"suggestionId"}},{"kind":"Field","name":{"kind":"Name","value":"actionCard"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"kind"}},{"kind":"Field","name":{"kind":"Name","value":"title"}},{"kind":"Field","name":{"kind":"Name","value":"summary"}},{"kind":"Field","name":{"kind":"Name","value":"fields"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"value"}}]}},{"kind":"Field","name":{"kind":"Name","value":"links"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"entityType"}},{"kind":"Field","name":{"kind":"Name","value":"entityId"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}}]}},{"kind":"Field","name":{"kind":"Name","value":"units"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}}]}}]}},{"kind":"Field","name":{"kind":"Name","value":"sentAt"}}]}},{"kind":"FragmentDefinition","name":{"kind":"Name","value":"TaskFields"},"typeCondition":{"kind":"NamedType","name":{"kind":"Name","value":"TaskType"}},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"taskNumber"}},{"kind":"Field","name":{"kind":"Name","value":"title"}},{"kind":"Field","name":{"kind":"Name","value":"taskStatus"}},{"kind":"Field","name":{"kind":"Name","value":"taskMode"}},{"kind":"Field","name":{"kind":"Name","value":"source"}},{"kind":"Field","name":{"kind":"Name","value":"category"}},{"kind":"Field","name":{"kind":"Name","value":"urgency"}},{"kind":"Field","name":{"kind":"Name","value":"priority"}},{"kind":"Field","name":{"kind":"Name","value":"confidential"}},{"kind":"Field","name":{"kind":"Name","value":"lastMessageAt"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}},{"kind":"Field","name":{"kind":"Name","value":"unitId"}},{"kind":"Field","name":{"kind":"Name","value":"createdAt"}},{"kind":"Field","name":{"kind":"Name","value":"tenantName"}},{"kind":"Field","name":{"kind":"Name","value":"unitLabel"}},{"kind":"Field","name":{"kind":"Name","value":"aiTriageSuggestion"}},{"kind":"Field","name":{"kind":"Name","value":"vendorAssigned"}},{"kind":"Field","name":{"kind":"Name","value":"requireVendorType"}},{"kind":"Field","name":{"kind":"Name","value":"assignedVendorId"}},{"kind":"Field","name":{"kind":"Name","value":"assignedVendorName"}},{"kind":"Field","name":{"kind":"Name","value":"aiConversationId"}},{"kind":"Field","name":{"kind":"Name","value":"parentConversationId"}},{"kind":"Field","name":{"kind":"Name","value":"externalConversationIds"}},{"kind":"Field","name":{"kind":"Name","value":"steps"}},{"kind":"Field","name":{"kind":"Name","value":"goal"}},{"kind":"Field","name":{"kind":"Name","value":"suggestionOptions"}},{"kind":"Field","name":{"kind":"Name","value":"lastReviewedAt"}},{"kind":"Field","name":{"kind":"Name","value":"lastReviewStatus"}},{"kind":"Field","name":{"kind":"Name","value":"lastReviewSummary"}},{"kind":"Field","name":{"kind":"Name","value":"lastReviewNextStep"}},{"kind":"Field","name":{"kind":"Name","value":"linkedConversations"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"conversationType"}},{"kind":"Field","name":{"kind":"Name","value":"lastMessageAt"}},{"kind":"Field","name":{"kind":"Name","value":"messageCount"}},{"kind":"Field","name":{"kind":"Name","value":"participants"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"participantType"}},{"kind":"Field","name":{"kind":"Name","value":"entityId"}},{"kind":"Field","name":{"kind":"Name","value":"portalUrl"}}]}}]}},{"kind":"Field","name":{"kind":"Name","value":"messages"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"FragmentSpread","name":{"kind":"Name","value":"TaskMessageFields"}}]}}]}}]} as unknown as DocumentNode<TasksQuery, TasksQueryVariables>;
export const TaskDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"Task"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"task"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"FragmentSpread","name":{"kind":"Name","value":"TaskFields"}}]}}]}},{"kind":"FragmentDefinition","name":{"kind":"Name","value":"TaskMessageFields"},"typeCondition":{"kind":"NamedType","name":{"kind":"Name","value":"ChatMessageType"}},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"body"}},{"kind":"Field","name":{"kind":"Name","value":"messageType"}},{"kind":"Field","name":{"kind":"Name","value":"senderName"}},{"kind":"Field","name":{"kind":"Name","value":"senderType"}},{"kind":"Field","name":{"kind":"Name","value":"isAi"}},{"kind":"Field","name":{"kind":"Name","value":"isSystem"}},{"kind":"Field","name":{"kind":"Name","value":"draftReply"}},{"kind":"Field","name":{"kind":"Name","value":"approvalStatus"}},{"kind":"Field","name":{"kind":"Name","value":"relatedTaskIds"}},{"kind":"Field","name":{"kind":"Name","value":"suggestionId"}},{"kind":"Field","name":{"kind":"Name","value":"actionCard"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"kind"}},{"kind":"Field","name":{"kind":"Name","value":"title"}},{"kind":"Field","name":{"kind":"Name","value":"summary"}},{"kind":"Field","name":{"kind":"Name","value":"fields"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"value"}}]}},{"kind":"Field","name":{"kind":"Name","value":"links"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"entityType"}},{"kind":"Field","name":{"kind":"Name","value":"entityId"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}}]}},{"kind":"Field","name":{"kind":"Name","value":"units"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}}]}}]}},{"kind":"Field","name":{"kind":"Name","value":"sentAt"}}]}},{"kind":"FragmentDefinition","name":{"kind":"Name","value":"TaskFields"},"typeCondition":{"kind":"NamedType","name":{"kind":"Name","value":"TaskType"}},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"taskNumber"}},{"kind":"Field","name":{"kind":"Name","value":"title"}},{"kind":"Field","name":{"kind":"Name","value":"taskStatus"}},{"kind":"Field","name":{"kind":"Name","value":"taskMode"}},{"kind":"Field","name":{"kind":"Name","value":"source"}},{"kind":"Field","name":{"kind":"Name","value":"category"}},{"kind":"Field","name":{"kind":"Name","value":"urgency"}},{"kind":"Field","name":{"kind":"Name","value":"priority"}},{"kind":"Field","name":{"kind":"Name","value":"confidential"}},{"kind":"Field","name":{"kind":"Name","value":"lastMessageAt"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}},{"kind":"Field","name":{"kind":"Name","value":"unitId"}},{"kind":"Field","name":{"kind":"Name","value":"createdAt"}},{"kind":"Field","name":{"kind":"Name","value":"tenantName"}},{"kind":"Field","name":{"kind":"Name","value":"unitLabel"}},{"kind":"Field","name":{"kind":"Name","value":"aiTriageSuggestion"}},{"kind":"Field","name":{"kind":"Name","value":"vendorAssigned"}},{"kind":"Field","name":{"kind":"Name","value":"requireVendorType"}},{"kind":"Field","name":{"kind":"Name","value":"assignedVendorId"}},{"kind":"Field","name":{"kind":"Name","value":"assignedVendorName"}},{"kind":"Field","name":{"kind":"Name","value":"aiConversationId"}},{"kind":"Field","name":{"kind":"Name","value":"parentConversationId"}},{"kind":"Field","name":{"kind":"Name","value":"externalConversationIds"}},{"kind":"Field","name":{"kind":"Name","value":"steps"}},{"kind":"Field","name":{"kind":"Name","value":"goal"}},{"kind":"Field","name":{"kind":"Name","value":"suggestionOptions"}},{"kind":"Field","name":{"kind":"Name","value":"lastReviewedAt"}},{"kind":"Field","name":{"kind":"Name","value":"lastReviewStatus"}},{"kind":"Field","name":{"kind":"Name","value":"lastReviewSummary"}},{"kind":"Field","name":{"kind":"Name","value":"lastReviewNextStep"}},{"kind":"Field","name":{"kind":"Name","value":"linkedConversations"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"conversationType"}},{"kind":"Field","name":{"kind":"Name","value":"lastMessageAt"}},{"kind":"Field","name":{"kind":"Name","value":"messageCount"}},{"kind":"Field","name":{"kind":"Name","value":"participants"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"participantType"}},{"kind":"Field","name":{"kind":"Name","value":"entityId"}},{"kind":"Field","name":{"kind":"Name","value":"portalUrl"}}]}}]}},{"kind":"Field","name":{"kind":"Name","value":"messages"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"FragmentSpread","name":{"kind":"Name","value":"TaskMessageFields"}}]}}]}}]} as unknown as DocumentNode<TaskQuery, TaskQueryVariables>;
export const SuggestionsDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"Suggestions"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"status"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"SuggestionStatus"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"suggestions"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"status"},"value":{"kind":"Variable","name":{"kind":"Name","value":"status"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"title"}},{"kind":"Field","name":{"kind":"Name","value":"body"}},{"kind":"Field","name":{"kind":"Name","value":"category"}},{"kind":"Field","name":{"kind":"Name","value":"urgency"}},{"kind":"Field","name":{"kind":"Name","value":"status"}},{"kind":"Field","name":{"kind":"Name","value":"source"}},{"kind":"Field","name":{"kind":"Name","value":"automationKey"}},{"kind":"Field","name":{"kind":"Name","value":"options"}},{"kind":"Field","name":{"kind":"Name","value":"actionTaken"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}},{"kind":"Field","name":{"kind":"Name","value":"unitId"}},{"kind":"Field","name":{"kind":"Name","value":"taskId"}},{"kind":"Field","name":{"kind":"Name","value":"vendorName"}},{"kind":"Field","name":{"kind":"Name","value":"propertyName"}},{"kind":"Field","name":{"kind":"Name","value":"draftMessage"}},{"kind":"Field","name":{"kind":"Name","value":"createdAt"}},{"kind":"Field","name":{"kind":"Name","value":"messages"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"body"}},{"kind":"Field","name":{"kind":"Name","value":"messageType"}},{"kind":"Field","name":{"kind":"Name","value":"senderName"}},{"kind":"Field","name":{"kind":"Name","value":"isAi"}},{"kind":"Field","name":{"kind":"Name","value":"isSystem"}},{"kind":"Field","name":{"kind":"Name","value":"sentAt"}},{"kind":"Field","name":{"kind":"Name","value":"draftReply"}},{"kind":"Field","name":{"kind":"Name","value":"approvalStatus"}}]}}]}}]}}]} as unknown as DocumentNode<SuggestionsQuery, SuggestionsQueryVariables>;
export const ConversationMessagesDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"ConversationMessages"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"conversationMessages"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"FragmentSpread","name":{"kind":"Name","value":"TaskMessageFields"}}]}}]}},{"kind":"FragmentDefinition","name":{"kind":"Name","value":"TaskMessageFields"},"typeCondition":{"kind":"NamedType","name":{"kind":"Name","value":"ChatMessageType"}},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"body"}},{"kind":"Field","name":{"kind":"Name","value":"messageType"}},{"kind":"Field","name":{"kind":"Name","value":"senderName"}},{"kind":"Field","name":{"kind":"Name","value":"senderType"}},{"kind":"Field","name":{"kind":"Name","value":"isAi"}},{"kind":"Field","name":{"kind":"Name","value":"isSystem"}},{"kind":"Field","name":{"kind":"Name","value":"draftReply"}},{"kind":"Field","name":{"kind":"Name","value":"approvalStatus"}},{"kind":"Field","name":{"kind":"Name","value":"relatedTaskIds"}},{"kind":"Field","name":{"kind":"Name","value":"suggestionId"}},{"kind":"Field","name":{"kind":"Name","value":"actionCard"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"kind"}},{"kind":"Field","name":{"kind":"Name","value":"title"}},{"kind":"Field","name":{"kind":"Name","value":"summary"}},{"kind":"Field","name":{"kind":"Name","value":"fields"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"value"}}]}},{"kind":"Field","name":{"kind":"Name","value":"links"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"entityType"}},{"kind":"Field","name":{"kind":"Name","value":"entityId"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}}]}},{"kind":"Field","name":{"kind":"Name","value":"units"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}}]}}]}},{"kind":"Field","name":{"kind":"Name","value":"sentAt"}}]}}]} as unknown as DocumentNode<ConversationMessagesQuery, ConversationMessagesQueryVariables>;
export const VendorsDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"Vendors"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"vendors"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"company"}},{"kind":"Field","name":{"kind":"Name","value":"vendorType"}},{"kind":"Field","name":{"kind":"Name","value":"phone"}},{"kind":"Field","name":{"kind":"Name","value":"email"}},{"kind":"Field","name":{"kind":"Name","value":"notes"}},{"kind":"Field","name":{"kind":"Name","value":"context"}},{"kind":"Field","name":{"kind":"Name","value":"portalUrl"}},{"kind":"Field","name":{"kind":"Name","value":"createdAt"}}]}}]}}]} as unknown as DocumentNode<VendorsQuery, VendorsQueryVariables>;
export const VendorTypesDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"VendorTypes"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"vendorTypes"}}]}}]} as unknown as DocumentNode<VendorTypesQuery, VendorTypesQueryVariables>;
export const EntityNoteDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"EntityNote"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"entityType"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"entityId"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"entityNote"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"entityType"},"value":{"kind":"Variable","name":{"kind":"Name","value":"entityType"}}},{"kind":"Argument","name":{"kind":"Name","value":"entityId"},"value":{"kind":"Variable","name":{"kind":"Name","value":"entityId"}}}]}]}}]} as unknown as DocumentNode<EntityNoteQuery, EntityNoteQueryVariables>;
export const GetDocumentDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"GetDocument"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"document"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"filename"}},{"kind":"Field","name":{"kind":"Name","value":"documentType"}},{"kind":"Field","name":{"kind":"Name","value":"status"}},{"kind":"Field","name":{"kind":"Name","value":"progress"}},{"kind":"Field","name":{"kind":"Name","value":"extractedData"}},{"kind":"Field","name":{"kind":"Name","value":"extractionMeta"}},{"kind":"Field","name":{"kind":"Name","value":"context"}},{"kind":"Field","name":{"kind":"Name","value":"rawText"}},{"kind":"Field","name":{"kind":"Name","value":"errorMessage"}},{"kind":"Field","name":{"kind":"Name","value":"createdAt"}},{"kind":"Field","name":{"kind":"Name","value":"processedAt"}}]}}]}}]} as unknown as DocumentNode<GetDocumentQuery, GetDocumentQueryVariables>;
export const GetConversationsDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"GetConversations"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"conversationType"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"ConversationType"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"limit"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"conversations"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"conversationType"},"value":{"kind":"Variable","name":{"kind":"Name","value":"conversationType"}}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"Variable","name":{"kind":"Name","value":"limit"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"conversationType"}},{"kind":"Field","name":{"kind":"Name","value":"title"}},{"kind":"Field","name":{"kind":"Name","value":"lastMessageAt"}},{"kind":"Field","name":{"kind":"Name","value":"updatedAt"}},{"kind":"Field","name":{"kind":"Name","value":"lastMessageBody"}},{"kind":"Field","name":{"kind":"Name","value":"lastMessageSenderName"}},{"kind":"Field","name":{"kind":"Name","value":"propertyName"}},{"kind":"Field","name":{"kind":"Name","value":"participantCount"}},{"kind":"Field","name":{"kind":"Name","value":"unreadCount"}}]}}]}}]} as unknown as DocumentNode<GetConversationsQuery, GetConversationsQueryVariables>;
export const RoutinesDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"Routines"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"routines"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"prompt"}},{"kind":"Field","name":{"kind":"Name","value":"schedule"}},{"kind":"Field","name":{"kind":"Name","value":"scheduleDisplay"}},{"kind":"Field","name":{"kind":"Name","value":"enabled"}},{"kind":"Field","name":{"kind":"Name","value":"state"}},{"kind":"Field","name":{"kind":"Name","value":"repeat"}},{"kind":"Field","name":{"kind":"Name","value":"completedCount"}},{"kind":"Field","name":{"kind":"Name","value":"nextRunAt"}},{"kind":"Field","name":{"kind":"Name","value":"lastRunAt"}},{"kind":"Field","name":{"kind":"Name","value":"lastStatus"}},{"kind":"Field","name":{"kind":"Name","value":"lastOutput"}},{"kind":"Field","name":{"kind":"Name","value":"createdAt"}}]}}]}}]} as unknown as DocumentNode<RoutinesQuery, RoutinesQueryVariables>;
export const RoutineDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"Routine"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"routine"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"prompt"}},{"kind":"Field","name":{"kind":"Name","value":"schedule"}},{"kind":"Field","name":{"kind":"Name","value":"scheduleDisplay"}},{"kind":"Field","name":{"kind":"Name","value":"isDefault"}},{"kind":"Field","name":{"kind":"Name","value":"enabled"}},{"kind":"Field","name":{"kind":"Name","value":"state"}},{"kind":"Field","name":{"kind":"Name","value":"repeat"}},{"kind":"Field","name":{"kind":"Name","value":"completedCount"}},{"kind":"Field","name":{"kind":"Name","value":"nextRunAt"}},{"kind":"Field","name":{"kind":"Name","value":"lastRunAt"}},{"kind":"Field","name":{"kind":"Name","value":"lastStatus"}},{"kind":"Field","name":{"kind":"Name","value":"lastOutput"}},{"kind":"Field","name":{"kind":"Name","value":"simulatedAt"}},{"kind":"Field","name":{"kind":"Name","value":"createdAt"}}]}}]}}]} as unknown as DocumentNode<RoutineQuery, RoutineQueryVariables>;
export const MeDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"Me"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"me"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"username"}}]}}]}}]} as unknown as DocumentNode<MeQuery, MeQueryVariables>;
export const LoginDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"Login"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"input"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"LoginInput"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"login"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"input"},"value":{"kind":"Variable","name":{"kind":"Name","value":"input"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"token"}},{"kind":"Field","name":{"kind":"Name","value":"user"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"username"}}]}}]}}]}}]} as unknown as DocumentNode<LoginMutation, LoginMutationVariables>;
export const CreatePropertyDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"CreateProperty"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"input"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"CreatePropertyInput"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"createProperty"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"input"},"value":{"kind":"Variable","name":{"kind":"Name","value":"input"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"address"}},{"kind":"Field","name":{"kind":"Name","value":"propertyType"}},{"kind":"Field","name":{"kind":"Name","value":"source"}},{"kind":"Field","name":{"kind":"Name","value":"units"}},{"kind":"Field","name":{"kind":"Name","value":"occupiedUnits"}},{"kind":"Field","name":{"kind":"Name","value":"monthlyRevenue"}},{"kind":"Field","name":{"kind":"Name","value":"unitList"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"isOccupied"}}]}}]}}]}}]} as unknown as DocumentNode<CreatePropertyMutation, CreatePropertyMutationVariables>;
export const UpdatePropertyDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"UpdateProperty"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"input"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"UpdatePropertyInput"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"updateProperty"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"input"},"value":{"kind":"Variable","name":{"kind":"Name","value":"input"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"address"}},{"kind":"Field","name":{"kind":"Name","value":"propertyType"}}]}}]}}]} as unknown as DocumentNode<UpdatePropertyMutation, UpdatePropertyMutationVariables>;
export const DeletePropertyDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"DeleteProperty"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"deleteProperty"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}}]}]}}]} as unknown as DocumentNode<DeletePropertyMutation, DeletePropertyMutationVariables>;
export const AddLeaseForTenantDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"AddLeaseForTenant"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"input"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"AddLeaseForTenantInput"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"addLeaseForTenant"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"input"},"value":{"kind":"Variable","name":{"kind":"Name","value":"input"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"email"}},{"kind":"Field","name":{"kind":"Name","value":"unitLabel"}},{"kind":"Field","name":{"kind":"Name","value":"leaseEndDate"}},{"kind":"Field","name":{"kind":"Name","value":"rentAmount"}},{"kind":"Field","name":{"kind":"Name","value":"paymentStatus"}},{"kind":"Field","name":{"kind":"Name","value":"isActive"}}]}}]}}]} as unknown as DocumentNode<AddLeaseForTenantMutation, AddLeaseForTenantMutationVariables>;
export const CreateTenantWithLeaseDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"CreateTenantWithLease"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"input"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"CreateTenantWithLeaseInput"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"createTenantWithLease"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"input"},"value":{"kind":"Variable","name":{"kind":"Name","value":"input"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"email"}},{"kind":"Field","name":{"kind":"Name","value":"unitLabel"}},{"kind":"Field","name":{"kind":"Name","value":"leaseEndDate"}},{"kind":"Field","name":{"kind":"Name","value":"rentAmount"}},{"kind":"Field","name":{"kind":"Name","value":"paymentStatus"}},{"kind":"Field","name":{"kind":"Name","value":"isActive"}}]}}]}}]} as unknown as DocumentNode<CreateTenantWithLeaseMutation, CreateTenantWithLeaseMutationVariables>;
export const DeleteTenantDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"DeleteTenant"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"deleteTenant"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}}]}]}}]} as unknown as DocumentNode<DeleteTenantMutation, DeleteTenantMutationVariables>;
export const CreateTaskDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"CreateTask"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"input"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"CreateTaskInput"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"createTask"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"input"},"value":{"kind":"Variable","name":{"kind":"Name","value":"input"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"title"}},{"kind":"Field","name":{"kind":"Name","value":"taskStatus"}},{"kind":"Field","name":{"kind":"Name","value":"taskMode"}},{"kind":"Field","name":{"kind":"Name","value":"category"}},{"kind":"Field","name":{"kind":"Name","value":"urgency"}},{"kind":"Field","name":{"kind":"Name","value":"source"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}},{"kind":"Field","name":{"kind":"Name","value":"unitId"}},{"kind":"Field","name":{"kind":"Name","value":"aiConversationId"}},{"kind":"Field","name":{"kind":"Name","value":"createdAt"}}]}}]}}]} as unknown as DocumentNode<CreateTaskMutation, CreateTaskMutationVariables>;
export const UpdateTaskDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"UpdateTask"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"input"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"UpdateTaskInput"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"updateTask"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"input"},"value":{"kind":"Variable","name":{"kind":"Name","value":"input"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"category"}},{"kind":"Field","name":{"kind":"Name","value":"taskStatus"}},{"kind":"Field","name":{"kind":"Name","value":"taskMode"}},{"kind":"Field","name":{"kind":"Name","value":"urgency"}}]}}]}}]} as unknown as DocumentNode<UpdateTaskMutation, UpdateTaskMutationVariables>;
export const UpdateTaskStatusDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"UpdateTaskStatus"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"status"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"TaskStatus"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"updateTaskStatus"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}},{"kind":"Argument","name":{"kind":"Name","value":"status"},"value":{"kind":"Variable","name":{"kind":"Name","value":"status"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"taskStatus"}}]}}]}}]} as unknown as DocumentNode<UpdateTaskStatusMutation, UpdateTaskStatusMutationVariables>;
export const UpdateTaskGoalDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"UpdateTaskGoal"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"goal"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"updateTaskGoal"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}},{"kind":"Argument","name":{"kind":"Name","value":"goal"},"value":{"kind":"Variable","name":{"kind":"Name","value":"goal"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"goal"}}]}}]}}]} as unknown as DocumentNode<UpdateTaskGoalMutation, UpdateTaskGoalMutationVariables>;
export const DeleteTaskDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"DeleteTask"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"deleteTask"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}}]}]}}]} as unknown as DocumentNode<DeleteTaskMutation, DeleteTaskMutationVariables>;
export const AssignVendorToTaskDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"AssignVendorToTask"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"taskId"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"vendorId"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"assignVendorToTask"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"taskId"},"value":{"kind":"Variable","name":{"kind":"Name","value":"taskId"}}},{"kind":"Argument","name":{"kind":"Name","value":"vendorId"},"value":{"kind":"Variable","name":{"kind":"Name","value":"vendorId"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"assignedVendorId"}},{"kind":"Field","name":{"kind":"Name","value":"assignedVendorName"}}]}}]}}]} as unknown as DocumentNode<AssignVendorToTaskMutation, AssignVendorToTaskMutationVariables>;
export const ActOnSuggestionDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"ActOnSuggestion"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"action"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"editedBody"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"actOnSuggestion"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}},{"kind":"Argument","name":{"kind":"Name","value":"action"},"value":{"kind":"Variable","name":{"kind":"Name","value":"action"}}},{"kind":"Argument","name":{"kind":"Name","value":"editedBody"},"value":{"kind":"Variable","name":{"kind":"Name","value":"editedBody"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"status"}},{"kind":"Field","name":{"kind":"Name","value":"actionTaken"}},{"kind":"Field","name":{"kind":"Name","value":"taskId"}}]}}]}}]} as unknown as DocumentNode<ActOnSuggestionMutation, ActOnSuggestionMutationVariables>;
export const SendMessageDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"SendMessage"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"input"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"SendMessageInput"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"sendMessage"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"input"},"value":{"kind":"Variable","name":{"kind":"Name","value":"input"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"body"}},{"kind":"Field","name":{"kind":"Name","value":"messageType"}},{"kind":"Field","name":{"kind":"Name","value":"senderName"}},{"kind":"Field","name":{"kind":"Name","value":"senderType"}},{"kind":"Field","name":{"kind":"Name","value":"isAi"}},{"kind":"Field","name":{"kind":"Name","value":"isSystem"}},{"kind":"Field","name":{"kind":"Name","value":"draftReply"}},{"kind":"Field","name":{"kind":"Name","value":"approvalStatus"}},{"kind":"Field","name":{"kind":"Name","value":"suggestionId"}},{"kind":"Field","name":{"kind":"Name","value":"actionCard"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"kind"}},{"kind":"Field","name":{"kind":"Name","value":"title"}},{"kind":"Field","name":{"kind":"Name","value":"summary"}},{"kind":"Field","name":{"kind":"Name","value":"fields"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"value"}}]}},{"kind":"Field","name":{"kind":"Name","value":"links"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"entityType"}},{"kind":"Field","name":{"kind":"Name","value":"entityId"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}}]}},{"kind":"Field","name":{"kind":"Name","value":"units"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"label"}},{"kind":"Field","name":{"kind":"Name","value":"propertyId"}}]}}]}},{"kind":"Field","name":{"kind":"Name","value":"sentAt"}}]}}]}}]} as unknown as DocumentNode<SendMessageMutation, SendMessageMutationVariables>;
export const SendSmsDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"SendSms"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"vendorId"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"body"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"taskId"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"sendSms"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"vendorId"},"value":{"kind":"Variable","name":{"kind":"Name","value":"vendorId"}}},{"kind":"Argument","name":{"kind":"Name","value":"body"},"value":{"kind":"Variable","name":{"kind":"Name","value":"body"}}},{"kind":"Argument","name":{"kind":"Name","value":"taskId"},"value":{"kind":"Variable","name":{"kind":"Name","value":"taskId"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"body"}},{"kind":"Field","name":{"kind":"Name","value":"senderName"}},{"kind":"Field","name":{"kind":"Name","value":"sentAt"}}]}}]}}]} as unknown as DocumentNode<SendSmsMutation, SendSmsMutationVariables>;
export const DeleteConversationDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"DeleteConversation"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"deleteConversation"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}}]}]}}]} as unknown as DocumentNode<DeleteConversationMutation, DeleteConversationMutationVariables>;
export const CreateVendorDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"CreateVendor"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"input"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"CreateVendorInput"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"createVendor"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"input"},"value":{"kind":"Variable","name":{"kind":"Name","value":"input"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"company"}},{"kind":"Field","name":{"kind":"Name","value":"vendorType"}},{"kind":"Field","name":{"kind":"Name","value":"phone"}},{"kind":"Field","name":{"kind":"Name","value":"email"}},{"kind":"Field","name":{"kind":"Name","value":"notes"}},{"kind":"Field","name":{"kind":"Name","value":"portalUrl"}}]}}]}}]} as unknown as DocumentNode<CreateVendorMutation, CreateVendorMutationVariables>;
export const UpdateVendorDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"UpdateVendor"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"input"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"UpdateVendorInput"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"updateVendor"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"input"},"value":{"kind":"Variable","name":{"kind":"Name","value":"input"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"company"}},{"kind":"Field","name":{"kind":"Name","value":"vendorType"}},{"kind":"Field","name":{"kind":"Name","value":"phone"}},{"kind":"Field","name":{"kind":"Name","value":"email"}},{"kind":"Field","name":{"kind":"Name","value":"notes"}},{"kind":"Field","name":{"kind":"Name","value":"portalUrl"}}]}}]}}]} as unknown as DocumentNode<UpdateVendorMutation, UpdateVendorMutationVariables>;
export const DeleteVendorDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"DeleteVendor"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"deleteVendor"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}}]}]}}]} as unknown as DocumentNode<DeleteVendorMutation, DeleteVendorMutationVariables>;
export const UpdateEntityContextDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"UpdateEntityContext"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"entityType"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"entityId"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"context"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"updateEntityContext"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"entityType"},"value":{"kind":"Variable","name":{"kind":"Name","value":"entityType"}}},{"kind":"Argument","name":{"kind":"Name","value":"entityId"},"value":{"kind":"Variable","name":{"kind":"Name","value":"entityId"}}},{"kind":"Argument","name":{"kind":"Name","value":"context"},"value":{"kind":"Variable","name":{"kind":"Name","value":"context"}}}]}]}}]} as unknown as DocumentNode<UpdateEntityContextMutation, UpdateEntityContextMutationVariables>;
export const SaveEntityNoteDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"SaveEntityNote"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"entityType"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"entityId"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"content"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"saveEntityNote"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"entityType"},"value":{"kind":"Variable","name":{"kind":"Name","value":"entityType"}}},{"kind":"Argument","name":{"kind":"Name","value":"entityId"},"value":{"kind":"Variable","name":{"kind":"Name","value":"entityId"}}},{"kind":"Argument","name":{"kind":"Name","value":"content"},"value":{"kind":"Variable","name":{"kind":"Name","value":"content"}}}]}]}}]} as unknown as DocumentNode<SaveEntityNoteMutation, SaveEntityNoteMutationVariables>;
export const CreateRoutineDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"CreateRoutine"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"name"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"prompt"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"schedule"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"repeat"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"createRoutine"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"name"},"value":{"kind":"Variable","name":{"kind":"Name","value":"name"}}},{"kind":"Argument","name":{"kind":"Name","value":"prompt"},"value":{"kind":"Variable","name":{"kind":"Name","value":"prompt"}}},{"kind":"Argument","name":{"kind":"Name","value":"schedule"},"value":{"kind":"Variable","name":{"kind":"Name","value":"schedule"}}},{"kind":"Argument","name":{"kind":"Name","value":"repeat"},"value":{"kind":"Variable","name":{"kind":"Name","value":"repeat"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}}]}}]}}]} as unknown as DocumentNode<CreateRoutineMutation, CreateRoutineMutationVariables>;
export const UpdateRoutineDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"UpdateRoutine"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"name"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"prompt"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"schedule"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"enabled"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"Boolean"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"updateRoutine"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}},{"kind":"Argument","name":{"kind":"Name","value":"name"},"value":{"kind":"Variable","name":{"kind":"Name","value":"name"}}},{"kind":"Argument","name":{"kind":"Name","value":"prompt"},"value":{"kind":"Variable","name":{"kind":"Name","value":"prompt"}}},{"kind":"Argument","name":{"kind":"Name","value":"schedule"},"value":{"kind":"Variable","name":{"kind":"Name","value":"schedule"}}},{"kind":"Argument","name":{"kind":"Name","value":"enabled"},"value":{"kind":"Variable","name":{"kind":"Name","value":"enabled"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}}]}}]}}]} as unknown as DocumentNode<UpdateRoutineMutation, UpdateRoutineMutationVariables>;
export const DeleteRoutineDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"DeleteRoutine"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"deleteRoutine"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}}]}]}}]} as unknown as DocumentNode<DeleteRoutineMutation, DeleteRoutineMutationVariables>;
export const RunRoutineDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"mutation","name":{"kind":"Name","value":"RunRoutine"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"uid"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"runRoutine"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"uid"},"value":{"kind":"Variable","name":{"kind":"Name","value":"uid"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"uid"}},{"kind":"Field","name":{"kind":"Name","value":"lastStatus"}},{"kind":"Field","name":{"kind":"Name","value":"lastOutput"}},{"kind":"Field","name":{"kind":"Name","value":"lastRunAt"}}]}}]}}]} as unknown as DocumentNode<RunRoutineMutation, RunRoutineMutationVariables>;