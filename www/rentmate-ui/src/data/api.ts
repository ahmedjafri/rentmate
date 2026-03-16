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
  title
  isTask
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
  query {
    tasks(status: "suggested", source: "ai_suggestion") {
      ${TASK_FIELDS}
    }
  }
`;

export const ADD_TASK_MESSAGE_MUTATION = `
  mutation AddTaskMessage($input: AddTaskMessageInput!) {
    addTaskMessage(input: $input) {
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

export const UPDATE_TASK_MUTATION = `
  mutation UpdateTask($input: UpdateTaskInput!) {
    updateTask(input: $input) {
      uid
      taskStatus
      taskMode
    }
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
      createdAt
    }
  }
`;
