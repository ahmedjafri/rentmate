import { getToken } from '@/lib/auth';

const GRAPHQL_URL = '/graphql';

export async function graphqlQuery<T = Record<string, unknown>>(
  query: string,
  variables: Record<string, unknown> = {},
): Promise<T> {
  const token = getToken();
  const res = await fetch(GRAPHQL_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ query, variables }),
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
  return data as T;
}

export const HOUSES_QUERY = `
  query {
    houses {
      uid
      name
      address
      propertyType
      source
      units
      occupiedUnits
      monthlyRevenue
      unitList { uid label isOccupied }
      tenants { uid name }
      leases {
        uid
        startDate
        endDate
        rentAmount
        tenant { uid name }
      }
    }
  }
`;

export const UPDATE_PROPERTY_MUTATION = `
  mutation UpdateProperty($input: UpdatePropertyInput!) {
    updateProperty(input: $input) {
      uid
      name
      address
      propertyType
    }
  }
`;

export const DELETE_PROPERTY_MUTATION = `
  mutation DeleteProperty($uid: String!) {
    deleteProperty(uid: $uid)
  }
`;

export const CREATE_PROPERTY_MUTATION = `
  mutation CreateProperty($input: CreatePropertyInput!) {
    createProperty(input: $input) {
      uid
      name
      address
      propertyType
      source
      units
      occupiedUnits
      monthlyRevenue
      unitList { uid label isOccupied }
    }
  }
`;

export const TENANTS_QUERY = `
  query {
    tenants {
      uid
      name
      email
      unitLabel
      leaseEndDate
      rentAmount
      paymentStatus
      isActive
      extraProperties { key value }
      rents { uid name address }
      leases {
        uid
        rentAmount
        startDate
        endDate
      }
    }
  }
`;

const TASK_FIELDS = `
  uid
  taskNumber
  title
  taskStatus
  taskMode
  source
  category
  urgency
  priority
  confidential
  lastMessageAt
  propertyId
  unitId
  createdAt
  tenantName
  unitLabel
  aiTriageSuggestion
  vendorAssigned
  requireVendorType
  assignedVendorId
  assignedVendorName
  aiConversationId
  parentConversationId
  externalConversationId
  suggestionOptions
  messages {
    uid
    body
    messageType
    senderName
    isAi
    isSystem
    draftReply
    approvalStatus
    relatedTaskIds
    suggestionId
    sentAt
  }
`;

export const TASKS_QUERY = `
  query Tasks($category: String, $status: String, $source: String) {
    tasks(category: $category, status: $status, source: $source) {
      ${TASK_FIELDS}
    }
  }
`;

export const TASK_QUERY = `
  query Task($uid: String!) {
    task(uid: $uid) {
      ${TASK_FIELDS}
    }
  }
`;

export const SUGGESTIONS_QUERY = `
  query Suggestions($status: String) {
    suggestions(status: $status) {
      uid
      title
      body
      category
      urgency
      status
      source
      automationKey
      options
      actionTaken
      propertyId
      unitId
      taskId
      vendorName
      propertyName
      draftMessage
      createdAt
      messages {
        uid
        body
        messageType
        senderName
        isAi
        isSystem
        sentAt
        draftReply
        approvalStatus
      }
    }
  }
`;

export const ACT_ON_SUGGESTION_MUTATION = `
  mutation ActOnSuggestion($uid: String!, $action: String!, $editedBody: String) {
    actOnSuggestion(uid: $uid, action: $action, editedBody: $editedBody) {
      uid
      status
      actionTaken
      taskId
    }
  }
`;

export const SEND_MESSAGE_MUTATION = `
  mutation SendMessage($input: SendMessageInput!) {
    sendMessage(input: $input) {
      uid
      body
      messageType
      senderName
      isAi
      isSystem
      sentAt
    }
  }
`;

export const SEND_SMS_MUTATION = `
  mutation SendSms($vendorId: String!, $body: String!, $taskId: String) {
    sendSms(vendorId: $vendorId, body: $body, taskId: $taskId) {
      uid
      body
      senderName
      sentAt
    }
  }
`;

export const DELETE_CONVERSATION_MUTATION = `
  mutation DeleteConversation($uid: String!) {
    deleteConversation(uid: $uid)
  }
`;

export const UPDATE_TASK_MUTATION = `
  mutation UpdateTask($input: UpdateTaskInput!) {
    updateTask(input: $input) {
      uid
      taskStatus
      taskMode
    }
  }
`;

export const DELETE_TASK_MUTATION = `
  mutation DeleteTask($uid: String!) {
    deleteTask(uid: $uid)
  }
`;

export const ADD_DOCUMENT_TAG_MUTATION = `
  mutation AddDocumentTag($input: AddDocumentTagInput!) {
    addDocumentTag(input: $input) {
      uid
      documentId
      tagType
      propertyId
      unitId
      tenantId
    }
  }
`;

export const CONFIRM_DOCUMENT_MUTATION = `
  mutation ConfirmDocument($documentId: String!) {
    confirmDocument(documentId: $documentId)
  }
`;

export const ADD_LEASE_FOR_TENANT_MUTATION = `
  mutation AddLeaseForTenant($input: AddLeaseForTenantInput!) {
    addLeaseForTenant(input: $input) {
      uid
      name
      email
      unitLabel
      leaseEndDate
      rentAmount
      paymentStatus
      isActive
    }
  }
`;

export const CREATE_TENANT_WITH_LEASE_MUTATION = `
  mutation CreateTenantWithLease($input: CreateTenantWithLeaseInput!) {
    createTenantWithLease(input: $input) {
      uid
      name
      email
      unitLabel
      leaseEndDate
      rentAmount
      paymentStatus
      isActive
    }
  }
`;

export const CONVERSATION_MESSAGES_QUERY = `
  query ConversationMessages($uid: String!) {
    conversationMessages(uid: $uid) {
      uid
      body
      messageType
      senderName
      senderType
      isAi
      isSystem
      draftReply
      suggestionId
      sentAt
    }
  }
`;

export const CREATE_TASK_MUTATION = `
  mutation CreateTask($input: CreateTaskInput!) {
    createTask(input: $input) {
      uid
      title
      taskStatus
      taskMode
      category
      urgency
      source
      propertyId
      unitId
      aiConversationId
      createdAt
    }
  }
`;

export const VENDORS_QUERY = `
  query { vendors { uid name company vendorType phone email notes contactMethod inviteToken inviteStatus createdAt } }
`;

export const VENDOR_TYPES_QUERY = `
  query { vendorTypes }
`;

export const CREATE_VENDOR_MUTATION = `
  mutation CreateVendor($input: CreateVendorInput!) {
    createVendor(input: $input) { uid name company vendorType phone email notes contactMethod inviteToken inviteStatus }
  }
`;

export const UPDATE_VENDOR_MUTATION = `
  mutation UpdateVendor($input: UpdateVendorInput!) {
    updateVendor(input: $input) { uid name company vendorType phone email notes contactMethod inviteToken inviteStatus }
  }
`;

export const DELETE_VENDOR_MUTATION = `
  mutation DeleteVendor($uid: String!) { deleteVendor(uid: $uid) }
`;

export const ASSIGN_VENDOR_TO_TASK_MUTATION = `
  mutation AssignVendorToTask($taskId: String!, $vendorId: String!) {
    assignVendorToTask(taskId: $taskId, vendorId: $vendorId) {
      uid
      assignedVendorId
      assignedVendorName
    }
  }
`;
