export type SuggestionCategory = 'rent' | 'maintenance' | 'leasing' | 'compliance';
export type SuggestionUrgency = 'low' | 'medium' | 'high' | 'critical';
export type SuggestionStatus = 'pending' | 'accepted' | 'dismissed' | 'expired';
export type AutonomyLevel = 'manual' | 'suggest' | 'autonomous';
export type ActionPolicyLevel = 'strict' | 'balanced' | 'aggressive';
export type TicketPriority = 'low' | 'routine' | 'urgent' | 'emergency';
export type TicketStatus = 'open' | 'in_progress' | 'resolved' | 'closed';

export type TaskMode = 'autonomous' | 'waiting_approval' | 'manual';
export type TaskParticipantType = 'agent' | 'tenant' | 'vendor' | 'manager';
export type TaskStatus = 'active' | 'paused' | 'resolved' | 'cancelled';

export interface TaskParticipant {
  type: TaskParticipantType;
  name: string;
  id?: string;
}

export interface ConversationParticipant {
  name: string;
  participantType: string;
  entityId?: string | null;
  portalUrl?: string | null;
}

export interface LinkedConversation {
  uid: string;
  label: string;
  conversationType: string;
  lastMessageAt?: string | null;
  messageCount: number;
  participants: ConversationParticipant[];
}

export interface ActionDeskTask {
  id: string;
  taskNumber?: number | null;
  title: string;
  mode: TaskMode;
  status: TaskStatus;
  participants: TaskParticipant[];
  lastMessage: string;
  lastMessageBy: string;
  lastMessageAt: Date;
  unreadCount: number;
  propertyId?: string;
  category: SuggestionCategory;
  urgency: SuggestionUrgency;
  chatThread: ChatMessage[];
  confidential?: boolean;
  aiConversationId?: string | null;
  externalConversationIds?: string[];
  parentConversationId?: string | null;
  ancestorIds?: string[];
  requireVendorType?: string;
  assignedVendorId?: string;
  assignedVendorName?: string;
  steps?: { key: string; label: string; status: 'pending' | 'active' | 'done'; note?: string }[];
  goal?: string | null;
  suggestionOptions?: { key: string; label: string; action: string; variant: string }[];
  linkedConversations?: LinkedConversation[];
  lastReviewedAt?: string | null;
  lastReviewStatus?: string | null;
  lastReviewSummary?: string | null;
  lastReviewNextStep?: string | null;
}

export type ChatMessageType = 'message' | 'internal' | 'approval' | 'context' | 'error' | 'action';
export type ChatSenderType = 'manager' | 'ai' | 'tenant' | 'vendor';

export interface ChatMessageRelatedTask {
  taskId: string;
  label: string;
}

export interface ChatMessageAttachment {
  documentId: string;
  filename: string;
}

export interface ChatActionCardField {
  label: string;
  value: string;
}

export interface ChatActionCardLink {
  label: string;
  entityType: 'suggestion' | 'property' | 'tenant' | 'unit' | 'document';
  entityId: string;
  propertyId?: string | null;
}

export interface ChatActionCardUnit {
  uid: string;
  label: string;
  propertyId: string;
}

export interface ChatActionCard {
  kind: 'suggestion' | 'property' | 'tenant' | 'document' | 'question';
  title: string;
  summary?: string;
  fields?: ChatActionCardField[];
  links?: ChatActionCardLink[];
  units?: ChatActionCardUnit[];
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  suggestionId?: string;
  senderName?: string;
  senderType?: ChatSenderType;
  messageType?: ChatMessageType;
  draftReply?: string;
  approvalStatus?: 'pending' | 'approved' | 'rejected' | 'edited';
  relatedTasks?: ChatMessageRelatedTask[];
  attachments?: ChatMessageAttachment[];
  actionCard?: ChatActionCard;
}

export interface SuggestionOption {
  key: string;
  label: string;
  action: string;
  variant: string;
}

export interface Suggestion {
  id: string;
  title: string;
  body?: string;
  category: SuggestionCategory;
  urgency: SuggestionUrgency;
  status: SuggestionStatus;
  source?: string;
  automationKey?: string;
  options?: SuggestionOption[];
  actionTaken?: string;
  propertyId?: string;
  unitId?: string;
  taskId?: string;
  vendorName?: string;
  propertyName?: string;
  draftMessage?: string;
  createdAt: Date;
  chatThread: ChatMessage[];
}

export interface PropertyUnit {
  id: string;
  label: string;
  isOccupied: boolean;
  context?: string;
}

export interface Property {
  id: string;
  name: string;
  address: string;
  propertyType?: 'single_family' | 'multi_family';
  source?: 'manual' | 'document';
  context?: string;
  units: number;
  occupiedUnits: number;
  monthlyRevenue: number;
  unitList?: PropertyUnit[];
  image?: string;
}

export interface Tenant {
  id: string;
  name: string;
  email: string;
  unit: string;
  isActive: boolean;
  propertyId: string;
  leaseEnd: Date;
  rentAmount: number;
  paymentStatus: 'current' | 'late' | 'overdue';
  context?: string;
  portalUrl?: string;
}

export interface MaintenanceTicket {
  id: string;
  tenantId: string;
  tenantName: string;
  propertyId: string;
  unit: string;
  description: string;
  priority: TicketPriority;
  status: TicketStatus;
  createdAt: Date;
  aiTriageSuggestion?: string;
  vendorAssigned?: string;
}

export interface ActionPolicySettings {
  entity_changes: ActionPolicyLevel;
  outbound_messages: ActionPolicyLevel;
  suggestion_fallback: ActionPolicyLevel;
}

export interface NotificationItem {
  id: string;
  kind: string;
  channel: string;
  deliveryStatus: string;
  title: string;
  body?: string | null;
  taskId?: string | null;
  conversationId?: string | null;
  createdAt: Date;
  readAt?: Date | null;
  archivedAt?: Date | null;
  sentAt?: Date | null;
  failedAt?: Date | null;
  failureReason?: string | null;
  extra?: Record<string, unknown> | null;
}

export type DocumentStatus = 'uploading' | 'analyzing' | 'ready' | 'error';
export type DocumentType = 'lease' | 'invoice' | 'notice' | 'inspection' | 'insurance' | 'other';

export interface DocumentTag {
  type: 'property' | 'unit' | 'tenant';
  id: string;
  label: string;
}

export interface Vendor {
  id: string;
  name: string;
  company?: string;
  vendorType?: string;
  phone?: string;
  email?: string;
  notes?: string;
  context?: string;
  portalUrl?: string;
}

export interface ManagedDocument {
  id: string;
  fileName: string;
  fileType: string;
  fileSize: number;
  documentType: DocumentType;
  status: DocumentStatus;
  uploadedAt: Date;
  analyzedAt?: Date;
  tags: DocumentTag[];
  aiSummary?: string;
  aiExtracted?: Record<string, string>;
  actionDeskTaskId?: string;
  confirmed?: boolean;
  confirmedAt?: Date;
  errorMessage?: string;
  generatedByRentMate?: boolean;
  generationSource?: string;
}

// --- Mock Data ---

export const mockProperties: Property[] = [
  { id: 'p1', name: 'Sunset Apartments', address: '142 Sunset Blvd, Los Angeles, CA', units: 12, occupiedUnits: 11, monthlyRevenue: 28600 },
  { id: 'p2', name: 'Oak Grove Residences', address: '88 Oak St, Portland, OR', units: 8, occupiedUnits: 7, monthlyRevenue: 16800 },
  { id: 'p3', name: 'Riverside Condos', address: '320 River Rd, Austin, TX', units: 6, occupiedUnits: 6, monthlyRevenue: 14400 },
  { id: 'p4', name: 'Maple Heights', address: '55 Maple Ave, Denver, CO', units: 10, occupiedUnits: 8, monthlyRevenue: 20000 },
  { id: 'p5', name: 'Harbor View Lofts', address: '7 Harbor Ln, Seattle, WA', units: 4, occupiedUnits: 4, monthlyRevenue: 12000 },
];

export const mockTenants: Tenant[] = [
  { id: 't1', name: 'Sarah Chen', email: 'sarah@email.com', unit: '101', propertyId: 'p1', leaseEnd: new Date('2026-08-15'), rentAmount: 2600, paymentStatus: 'current', isActive: true },
  { id: 't2', name: 'Marcus Johnson', email: 'marcus@email.com', unit: '204', propertyId: 'p1', leaseEnd: new Date('2025-05-01'), rentAmount: 2400, paymentStatus: 'late', isActive: false },
  { id: 't3', name: 'Emily Rivera', email: 'emily@email.com', unit: '302', propertyId: 'p1', leaseEnd: new Date('2027-01-31'), rentAmount: 2800, paymentStatus: 'current', isActive: true },
  { id: 't4', name: 'David Kim', email: 'david@email.com', unit: '3A', propertyId: 'p2', leaseEnd: new Date('2026-09-30'), rentAmount: 2100, paymentStatus: 'current', isActive: true },
  { id: 't5', name: 'Lisa Patel', email: 'lisa@email.com', unit: '1B', propertyId: 'p2', leaseEnd: new Date('2025-04-15'), rentAmount: 2300, paymentStatus: 'overdue', isActive: false },
  { id: 't6', name: 'James Wu', email: 'james@email.com', unit: 'A1', propertyId: 'p3', leaseEnd: new Date('2026-12-31'), rentAmount: 2400, paymentStatus: 'current', isActive: true },
  { id: 't7', name: 'Ana Gomez', email: 'ana@email.com', unit: 'B2', propertyId: 'p3', leaseEnd: new Date('2026-07-15'), rentAmount: 2400, paymentStatus: 'current', isActive: true },
  { id: 't8', name: 'Robert Taylor', email: 'robert@email.com', unit: '5C', propertyId: 'p4', leaseEnd: new Date('2025-06-30'), rentAmount: 2500, paymentStatus: 'late', isActive: false },
  { id: 't9', name: 'Michelle Lee', email: 'michelle@email.com', unit: '2A', propertyId: 'p4', leaseEnd: new Date('2027-03-01'), rentAmount: 2500, paymentStatus: 'current', isActive: true },
  { id: 't10', name: 'Tom Baker', email: 'tom@email.com', unit: 'PH1', propertyId: 'p5', leaseEnd: new Date('2026-11-30'), rentAmount: 3000, paymentStatus: 'current', isActive: true },
  { id: 't11', name: 'Priya Sharma', email: 'priya@email.com', unit: '103', propertyId: 'p1', leaseEnd: new Date('2026-10-15'), rentAmount: 2550, paymentStatus: 'current', isActive: true },
  { id: 't12', name: 'Chris Anderson', email: 'chris@email.com', unit: '205', propertyId: 'p1', leaseEnd: new Date('2026-06-01'), rentAmount: 2400, paymentStatus: 'current', isActive: true },
];

export const mockSuggestions: Suggestion[] = [
  {
    id: 's1', category: 'rent', urgency: 'high', title: 'Raise rent for Unit 204 at lease renewal',
    description: 'Marcus Johnson\'s lease expires May 1. Market analysis shows comparable units rent for $2,700–$2,850. Current rent is $2,400.',
    recommendedAction: 'Increase rent to $2,650 (10.4% increase) with 60-day notice. This is still below market rate and accounts for tenant retention value.',
    confidence: 0.87, autonomyLevel: 'suggest', status: 'pending', propertyId: 'p1', tenantId: 't2',
    createdAt: new Date('2026-03-10'), chatThread: [],
  },
  {
    id: 's2', category: 'maintenance', urgency: 'critical', title: 'Water heater replacement — Sunset Apartments',
    description: 'Building water heater (unit serving floors 1–2) is 14 years old and showing corrosion. Last inspection flagged as "replace within 6 months" — that was 5 months ago.',
    recommendedAction: 'Schedule replacement with ProPlumb Co. (preferred vendor). Estimated cost: $3,200. Notify affected tenants 48 hours in advance.',
    confidence: 0.94, autonomyLevel: 'suggest', status: 'pending', propertyId: 'p1',
    createdAt: new Date('2026-03-09'), chatThread: [],
  },
  {
    id: 's3', category: 'rent', urgency: 'medium', title: 'Send late payment reminder to Lisa Patel',
    description: 'Lisa Patel (Unit 1B, Oak Grove) is 12 days overdue on March rent ($2,300). She was late once before in October but paid within 5 days of reminder.',
    recommendedAction: 'Send a friendly but firm reminder email. Mention the late fee policy and offer to set up autopay.',
    confidence: 0.91, autonomyLevel: 'suggest', status: 'pending', propertyId: 'p2', tenantId: 't5',
    createdAt: new Date('2026-03-11'), chatThread: [],
  },
  {
    id: 's4', category: 'leasing', urgency: 'high', title: 'Pre-lease Unit 1B — Oak Grove',
    description: 'Lisa Patel\'s lease ends April 15 and she has not confirmed renewal. Start marketing the unit now to minimize vacancy.',
    recommendedAction: 'List on Zillow, Apartments.com, and local boards at $2,450/mo (6.5% increase). Schedule photos this week.',
    confidence: 0.78, autonomyLevel: 'manual', status: 'pending', propertyId: 'p2', tenantId: 't5',
    createdAt: new Date('2026-03-10'), chatThread: [],
  },
  {
    id: 's5', category: 'compliance', urgency: 'high', title: 'Fire extinguisher inspection overdue — Maple Heights',
    description: 'Annual fire extinguisher inspection was due February 28. This is now 11 days overdue and could result in a code violation.',
    recommendedAction: 'Schedule inspection with SafeGuard Fire Services this week. Cost: ~$150. Update compliance calendar.',
    confidence: 0.96, autonomyLevel: 'suggest', status: 'pending', propertyId: 'p4',
    createdAt: new Date('2026-03-11'), chatThread: [],
  },
  {
    id: 's6', category: 'rent', urgency: 'low', title: 'Offer early renewal discount — Emily Rivera',
    description: 'Emily\'s lease doesn\'t expire until Jan 2026, but she\'s been an excellent tenant for 3 years. Offering early renewal locks in occupancy.',
    recommendedAction: 'Offer 2% rent increase (below market 5%) if she signs a 2-year renewal by June 1.',
    confidence: 0.72, autonomyLevel: 'manual', status: 'pending', propertyId: 'p1', tenantId: 't3',
    createdAt: new Date('2026-03-08'), chatThread: [],
  },
  {
    id: 's7', category: 'maintenance', urgency: 'medium', title: 'HVAC filter replacement — all Riverside units',
    description: 'Quarterly HVAC filter replacement is due for all 6 units at Riverside Condos. Last replaced Dec 12.',
    recommendedAction: 'Order 6 filters ($12 each) and schedule maintenance visit. Can be combined with spring inspection.',
    confidence: 0.93, autonomyLevel: 'autonomous', status: 'pending', propertyId: 'p3',
    createdAt: new Date('2026-03-07'), chatThread: [],
  },
  {
    id: 's8', category: 'maintenance', urgency: 'low', title: 'Spring maintenance notice — all properties',
    description: 'Spring is a good time to send a building-wide notice about upcoming maintenance, landscaping, and any seasonal reminders.',
    recommendedAction: 'Draft and send a friendly spring update email to all tenants. Include pest control schedule and AC check-up availability.',
    confidence: 0.85, autonomyLevel: 'suggest', status: 'pending',
    createdAt: new Date('2026-03-06'), chatThread: [],
  },
  {
    id: 's9', category: 'leasing', urgency: 'medium', title: 'Lease renewal conversation — David Kim',
    description: 'David\'s lease at Oak Grove (Unit 3A) expires Sep 30. He\'s been a reliable tenant. Start the conversation early.',
    recommendedAction: 'Send renewal inquiry email with proposed 3% increase ($2,163/mo). Mention building improvements planned for summer.',
    confidence: 0.81, autonomyLevel: 'suggest', status: 'pending', propertyId: 'p2', tenantId: 't4',
    createdAt: new Date('2026-03-09'), chatThread: [],
  },
  {
    id: 's10', category: 'rent', urgency: 'medium', title: 'Review rent pricing — Harbor View Lofts',
    description: 'Market rents in the Seattle waterfront area have increased 8% YoY. Current rents at Harbor View may be undervalued.',
    recommendedAction: 'Run a full market comparison and consider adjusting rents at next renewal cycle. Current avg: $3,000, market avg: $3,250.',
    confidence: 0.76, autonomyLevel: 'manual', status: 'pending', propertyId: 'p5',
    createdAt: new Date('2026-03-08'), chatThread: [],
  },
  {
    id: 's11', category: 'maintenance', urgency: 'high', title: 'Roof leak reported — Unit 5C, Maple Heights',
    description: 'Robert Taylor reported a ceiling stain growing in the bedroom. Likely a roof leak that needs immediate assessment.',
    recommendedAction: 'Dispatch roofing inspector within 24 hours. Place a tarp if rain is forecast. Notify tenant of timeline.',
    confidence: 0.89, autonomyLevel: 'suggest', status: 'pending', propertyId: 'p4', tenantId: 't8',
    createdAt: new Date('2026-03-11'), chatThread: [],
  },
  {
    id: 's12', category: 'compliance', urgency: 'medium', title: 'Update tenant emergency contacts',
    description: 'Annual emergency contact update is recommended. 4 tenants haven\'t updated their information in over 18 months.',
    recommendedAction: 'Send a bulk email requesting updated emergency contact information. Set a 2-week response deadline.',
    confidence: 0.88, autonomyLevel: 'autonomous', status: 'pending',
    createdAt: new Date('2026-03-07'), chatThread: [],
  },
  {
    id: 's13', category: 'compliance', urgency: 'high', title: 'Noise complaint mediation — Sunset Apts',
    description: 'Two separate tenants on floor 2 have complained about noise from Unit 204 (Marcus Johnson) on weekend evenings.',
    recommendedAction: 'Send a polite noise policy reminder to Unit 204. If complaints persist, schedule a mediation conversation.',
    confidence: 0.83, autonomyLevel: 'suggest', status: 'pending', propertyId: 'p1', tenantId: 't2',
    createdAt: new Date('2026-03-10'), chatThread: [],
  },
  {
    id: 's14', category: 'maintenance', urgency: 'low', title: 'Landscaping refresh — Oak Grove',
    description: 'Front entrance landscaping is looking dated. Curb appeal affects leasing success.',
    recommendedAction: 'Get quotes from 2 landscaping companies for spring refresh. Budget: $500–$800.',
    confidence: 0.70, autonomyLevel: 'manual', status: 'pending', propertyId: 'p2',
    createdAt: new Date('2026-03-05'), chatThread: [],
  },
  {
    id: 's15', category: 'rent', urgency: 'critical', title: 'Late fee application — Marcus Johnson',
    description: 'Marcus Johnson (Unit 204) rent is 8 days late. Per lease terms, a $75 late fee should be applied after 5 days.',
    recommendedAction: 'Apply $75 late fee to account and send automated notification. This is the 3rd late payment in 6 months.',
    confidence: 0.95, autonomyLevel: 'suggest', status: 'pending', propertyId: 'p1', tenantId: 't2',
    createdAt: new Date('2026-03-11'), chatThread: [],
  },
  {
    id: 's16', category: 'leasing', urgency: 'low', title: 'Virtual tour update — Sunset Apartments',
    description: 'The virtual tour for Sunset Apartments is 2 years old. Updated photos and virtual tour would improve listing conversion.',
    recommendedAction: 'Schedule a professional photographer. Estimated cost: $300–$500. Prioritize recently renovated units.',
    confidence: 0.74, autonomyLevel: 'manual', status: 'pending', propertyId: 'p1',
    createdAt: new Date('2026-03-04'), chatThread: [],
  },
  {
    id: 's17', category: 'compliance', urgency: 'low', title: 'Smoke detector battery replacement — Riverside',
    description: 'Semi-annual smoke detector check is due in April for all Riverside units. Schedule proactively.',
    recommendedAction: 'Purchase batteries in bulk and schedule unit-by-unit checks during spring maintenance week.',
    confidence: 0.90, autonomyLevel: 'autonomous', status: 'pending', propertyId: 'p3',
    createdAt: new Date('2026-03-06'), chatThread: [],
  },
  {
    id: 's18', category: 'maintenance', urgency: 'medium', title: 'Parking lot restriping — Maple Heights',
    description: 'Parking lot lines are fading and 2 tenants mentioned difficulty seeing spots at night.',
    recommendedAction: 'Get restriping quote. Typically $200–$400 for a 10-spot lot. Schedule for a dry weekend.',
    confidence: 0.79, autonomyLevel: 'suggest', status: 'pending', propertyId: 'p4',
    createdAt: new Date('2026-03-08'), chatThread: [],
  },
  {
    id: 's19', category: 'leasing', urgency: 'medium', title: 'Lease renewal follow-up — Robert Taylor',
    description: 'Robert was sent a renewal offer 2 weeks ago with no response. His lease expires June 30. Follow up to gauge intent.',
    recommendedAction: 'Call or text Robert to check if he received the offer and discuss any concerns. Prepare for potential vacancy.',
    confidence: 0.82, autonomyLevel: 'suggest', status: 'pending', propertyId: 'p4', tenantId: 't8',
    createdAt: new Date('2026-03-09'), chatThread: [],
  },
  {
    id: 's20', category: 'rent', urgency: 'low', title: 'Implement autopay incentive program',
    description: 'Only 40% of tenants use autopay. Offering a small discount could improve on-time payment rates significantly.',
    recommendedAction: 'Offer a $25/month discount for tenants who enroll in autopay. Projected to reduce late payments by 60%.',
    confidence: 0.68, autonomyLevel: 'manual', status: 'pending',
    createdAt: new Date('2026-03-03'), chatThread: [],
  },
];

export const mockTickets: MaintenanceTicket[] = [
  { id: 'mt1', tenantId: 't8', tenantName: 'Robert Taylor', propertyId: 'p4', unit: '5C', description: 'Ceiling stain growing in bedroom, possible roof leak', priority: 'urgent', status: 'open', createdAt: new Date('2026-03-11'), aiTriageSuggestion: 'Likely roof leak — dispatch inspector within 24hrs' },
  { id: 'mt2', tenantId: 't2', tenantName: 'Marcus Johnson', propertyId: 'p1', unit: '204', description: 'Kitchen faucet dripping constantly', priority: 'routine', status: 'in_progress', createdAt: new Date('2026-03-09'), aiTriageSuggestion: 'Standard faucet repair — schedule with general maintenance', vendorAssigned: 'QuickFix Plumbing' },
  { id: 'mt3', tenantId: 't6', tenantName: 'James Wu', propertyId: 'p3', unit: 'A1', description: 'AC not cooling properly, blowing warm air', priority: 'urgent', status: 'open', createdAt: new Date('2026-03-10'), aiTriageSuggestion: 'Could be refrigerant leak or compressor issue — HVAC tech needed' },
  { id: 'mt4', tenantId: 't11', tenantName: 'Priya Sharma', propertyId: 'p1', unit: '103', description: 'Bathroom exhaust fan making loud rattling noise', priority: 'low', status: 'open', createdAt: new Date('2026-03-08'), aiTriageSuggestion: 'Likely fan bearing — can wait for next maintenance round' },
  { id: 'mt5', tenantId: 't10', tenantName: 'Tom Baker', propertyId: 'p5', unit: 'PH1', description: 'Garage door opener stopped working', priority: 'routine', status: 'resolved', createdAt: new Date('2026-03-05'), vendorAssigned: 'Harbor Garage Services' },
  { id: 'mt6', tenantId: 't4', tenantName: 'David Kim', propertyId: 'p2', unit: '3A', description: 'Window seal broken, drafty in winter', priority: 'routine', status: 'open', createdAt: new Date('2026-03-07'), aiTriageSuggestion: 'Window seal replacement — schedule with glass specialist' },
  { id: 'mt7', tenantId: 't1', tenantName: 'Sarah Chen', propertyId: 'p1', unit: '101', description: 'Dishwasher not draining after cycle completes', priority: 'routine', status: 'in_progress', createdAt: new Date('2026-03-06'), aiTriageSuggestion: 'Check drain hose and filter first — may not need vendor', vendorAssigned: 'AppliancePro' },
];

export const defaultActionPolicySettings: ActionPolicySettings = {
  entity_changes: 'balanced',
  outbound_messages: 'balanced',
  suggestion_fallback: 'balanced',
};

export const categoryLabels: Record<SuggestionCategory, string> = {
  rent: 'Rent & Payments',
  maintenance: 'Maintenance',
  leasing: 'Leasing',
  compliance: 'Compliance',
};

export const urgencyColors: Record<SuggestionUrgency, string> = {
  low: 'bg-muted text-muted-foreground',
  medium: 'bg-primary/10 text-primary',
  high: 'bg-warning/15 text-warning-foreground',
  critical: 'bg-destructive/15 text-destructive',
};

export const categoryColors: Record<SuggestionCategory, string> = {
  rent: 'bg-primary/10 text-primary',
  maintenance: 'bg-warning/15 text-warning-foreground',
  leasing: 'bg-secondary text-secondary-foreground',
  compliance: 'bg-destructive/10 text-destructive',
};

export const autonomyLabels: Record<AutonomyLevel, string> = {
  manual: 'Manual',
  suggest: 'Suggest & Wait',
  autonomous: 'Fully Autonomous',
};

export const actionPolicyLabels: Record<ActionPolicyLevel, string> = {
  strict: 'Strict',
  balanced: 'Balanced',
  aggressive: 'Aggressive',
};

export const taskModeLabels: Record<TaskMode, string> = {
  autonomous: 'Autonomous',
  waiting_approval: 'Waiting Approval',
  manual: 'Manual',
};

export const mockActionDeskTasks: ActionDeskTask[] = [
  {
    id: 'ad1', title: 'Roof leak — Unit 5C, Maple Heights', mode: 'waiting_approval', status: 'active',
    participants: [{ type: 'agent', name: 'RentMate AI' }, { type: 'tenant', name: 'Robert Taylor' }, { type: 'vendor', name: 'Summit Roofing' }],
    lastMessage: 'Inspector can come Thursday at 10am. Should I confirm?', lastMessageBy: 'RentMate AI', lastMessageAt: new Date('2026-03-11T14:30:00'),
    unreadCount: 2, propertyId: 'p4', category: 'maintenance', urgency: 'high',
    chatThread: [
      { id: 'adc1-0', role: 'assistant', content: 'Robert Taylor (Unit 5C, Maple Heights) reported a growing ceiling stain in the bedroom — likely a roof leak requiring immediate attention. A maintenance ticket was filed and Summit Roofing has been contacted as the preferred vendor.', timestamp: new Date('2026-03-11T09:55:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'context' },
      { id: 'adc1-2', role: 'user', content: 'Hi, the stain on my bedroom ceiling is getting bigger. I think water is coming in when it rains.', timestamp: new Date('2026-03-11T10:05:00'), senderName: 'Robert Taylor', senderType: 'tenant', messageType: 'message' },
      { id: 'adc1-3', role: 'assistant', content: 'I\'ve contacted Summit Roofing to schedule an inspection. They\'re our preferred vendor for this building.', timestamp: new Date('2026-03-11T12:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'internal' },
      { id: 'adc1-4', role: 'user', content: 'We can have an inspector out Thursday at 10am or Friday at 2pm. Which works best?', timestamp: new Date('2026-03-11T13:30:00'), senderName: 'Summit Roofing', senderType: 'vendor', messageType: 'message' },
      { id: 'adc1-5', role: 'assistant', content: 'Inspector can come Thursday at 10am. Should I confirm?', timestamp: new Date('2026-03-11T14:30:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'approval', draftReply: 'Hi Summit Roofing, Thursday at 10am works great. Please confirm the appointment. We\'ll make sure someone is available to let you in. Thanks!', approvalStatus: 'pending' },
    ],
  },
  {
    id: 'ad2', title: 'Late rent follow-up — Lisa Patel', mode: 'autonomous', status: 'active',
    participants: [{ type: 'agent', name: 'RentMate AI' }, { type: 'tenant', name: 'Lisa Patel' }],
    lastMessage: 'Sent 2nd reminder with late fee notice. Lisa replied she will pay by Friday.', lastMessageBy: 'RentMate AI', lastMessageAt: new Date('2026-03-11T11:15:00'),
    unreadCount: 0, propertyId: 'p2', category: 'rent', urgency: 'medium',
    chatThread: [
      { id: 'adc2-0', role: 'assistant', content: 'Lisa Patel (Unit 1B, Oak Grove) is 12 days overdue on March rent ($2,300). She was late once before in October but paid within 5 days of a reminder. Auto-sending payment reminders per autonomy settings.', timestamp: new Date('2026-03-08T08:55:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'context' },
      { id: 'adc2-2', role: 'assistant', content: 'First reminder sent. No response after 3 days. Sending 2nd reminder with late fee notice.', timestamp: new Date('2026-03-11T09:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'internal' },
      { id: 'adc2-3', role: 'user', content: 'Sorry about the delay! I\'ll have the payment in by Friday. Can you waive the late fee this time?', timestamp: new Date('2026-03-11T11:00:00'), senderName: 'Lisa Patel', senderType: 'tenant', messageType: 'message' },
      { id: 'adc2-4', role: 'assistant', content: 'Lisa wants the late fee waived. She\'s been late once before (Oct). Should I waive it this time?', timestamp: new Date('2026-03-11T11:15:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'approval', draftReply: 'Hi Lisa, thank you for letting us know. We\'ll waive the late fee this time as a one-time courtesy. Please ensure payment is received by Friday. Going forward, we recommend setting up autopay to avoid any future late fees.', approvalStatus: 'pending' },
    ],
  },
  {
    id: 'ad3', title: 'Kitchen faucet repair — Unit 204', mode: 'autonomous', status: 'active',
    participants: [{ type: 'agent', name: 'RentMate AI' }, { type: 'tenant', name: 'Marcus Johnson' }, { type: 'vendor', name: 'QuickFix Plumbing' }],
    lastMessage: 'Vendor confirmed repair completed. Closing ticket after tenant confirmation.', lastMessageBy: 'RentMate AI', lastMessageAt: new Date('2026-03-11T09:45:00'),
    unreadCount: 0, propertyId: 'p1', category: 'maintenance', urgency: 'low',
    chatThread: [
      { id: 'adc3-0', role: 'assistant', content: 'Marcus Johnson (Unit 204, Sunset Apartments) reported a constantly dripping kitchen faucet. QuickFix Plumbing assigned as the vendor.', timestamp: new Date('2026-03-09T07:55:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'context' },
      { id: 'adc3-1', role: 'user', content: 'The kitchen faucet has been dripping nonstop. Can someone come fix it?', timestamp: new Date('2026-03-09T08:00:00'), senderName: 'Marcus Johnson', senderType: 'tenant', messageType: 'message' },
      { id: 'adc3-2', role: 'assistant', content: 'Scheduled QuickFix Plumbing for tomorrow 9-11am. Notified Marcus.', timestamp: new Date('2026-03-09T08:30:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'internal' },
      { id: 'adc3-3', role: 'user', content: 'Repair completed — standard cartridge replacement. Invoice: $85.', timestamp: new Date('2026-03-10T11:00:00'), senderName: 'QuickFix Plumbing', senderType: 'vendor', messageType: 'message' },
      { id: 'adc3-4', role: 'assistant', content: 'Vendor confirmed repair completed. Invoice $85 logged. Awaiting tenant confirmation to close.', timestamp: new Date('2026-03-11T09:45:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'internal' },
    ],
  },
  {
    id: 'ad4', title: 'Noise complaint — Priya Sharma (confidential)', mode: 'manual', status: 'resolved',
    participants: [{ type: 'agent', name: 'RentMate AI' }, { type: 'tenant', name: 'Priya Sharma' }],
    lastMessage: 'We\'ve sent a policy reminder to the tenant in question. We\'ll follow up if it continues.', lastMessageBy: 'RentMate AI', lastMessageAt: new Date('2026-03-10T18:00:00'),
    unreadCount: 0, propertyId: 'p1', category: 'compliance', urgency: 'medium', confidential: true,
    chatThread: [
      { id: 'adc4-0', role: 'assistant', content: 'Priya Sharma (Unit 103, Sunset Apartments) filed a noise complaint about the unit above her (Unit 204). She requests anonymity — this thread is confidential. A separate, non-identifying thread has been opened for the offending unit.', timestamp: new Date('2026-03-09T19:55:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'context', relatedTasks: [{ taskId: 'ad4b', label: 'Noise policy — Unit 204' }] },
      { id: 'adc4-1', role: 'user', content: 'The noise from the unit above me on weekends is really disruptive. It happens every Friday and Saturday night. I\'d prefer to keep this anonymous if possible.', timestamp: new Date('2026-03-09T20:00:00'), senderName: 'Priya Sharma', senderType: 'tenant', messageType: 'message' },
      { id: 'adc4-2', role: 'assistant', content: 'Received noise complaint from Priya (Unit 103) about Unit 204. She requests anonymity. I\'ve drafted a general noise policy reminder for the offending unit — no identifying info included. Please review before I send.', timestamp: new Date('2026-03-10T09:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'approval', draftReply: 'Hi Priya, thank you for letting us know. We take these concerns seriously and will address it with the tenant in question without sharing your identity. We\'ll follow up with you if we need any more details.', approvalStatus: 'approved' },
      { id: 'adc4-3', role: 'assistant', content: 'Sent acknowledgment to Priya (no names disclosed). Noise policy reminder sent to Unit 204 in a separate thread. Monitoring for further complaints.', timestamp: new Date('2026-03-10T18:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'internal' },
    ],
  },
  {
    id: 'ad4b', title: 'Noise policy reminder — Unit 204', mode: 'manual', status: 'resolved',
    participants: [{ type: 'agent', name: 'RentMate AI' }, { type: 'tenant', name: 'Marcus Johnson' }],
    lastMessage: 'Got it, I\'ll keep it down. Sorry about that.', lastMessageBy: 'Marcus Johnson', lastMessageAt: new Date('2026-03-10T17:00:00'),
    unreadCount: 1, propertyId: 'p1', category: 'compliance', urgency: 'medium',
    chatThread: [
      { id: 'adc4b-0', role: 'assistant', content: 'Neighbor feedback indicates weekend noise from Unit 204 (Marcus Johnson). A general noise policy reminder is being drafted — no complainant details will be shared.', timestamp: new Date('2026-03-10T09:55:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'context', relatedTasks: [{ taskId: 'ad4', label: 'Noise complaint (confidential)' }] },
      { id: 'adc4b-1', role: 'assistant', content: 'We\'ve received feedback from neighbors regarding noise on weekend evenings. Drafted a friendly reminder for Marcus — please approve before sending.', timestamp: new Date('2026-03-10T10:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'approval', draftReply: 'Hi Marcus, just a friendly reminder about our community quiet hours (10pm–8am). We\'ve had some general feedback about weekend noise levels. We appreciate your cooperation in keeping things comfortable for everyone. Let us know if you have any questions!', approvalStatus: 'approved' },
      { id: 'adc4b-2', role: 'user', content: 'Got it, I\'ll keep it down. Sorry about that.', timestamp: new Date('2026-03-10T17:00:00'), senderName: 'Marcus Johnson', senderType: 'tenant', messageType: 'message' },
      { id: 'adc4b-3', role: 'assistant', content: 'Marcus acknowledged the noise policy. No complainant details were shared. Monitoring for further complaints.', timestamp: new Date('2026-03-10T18:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'internal' },
    ],
  },
  {
    id: 'ad5', title: 'Lease renewal — David Kim', mode: 'waiting_approval', status: 'active',
    participants: [{ type: 'agent', name: 'RentMate AI' }, { type: 'tenant', name: 'David Kim' }],
    lastMessage: 'David asked about the 3% increase. Draft response ready for your review.', lastMessageBy: 'RentMate AI', lastMessageAt: new Date('2026-03-10T16:20:00'),
    unreadCount: 1, propertyId: 'p2', category: 'leasing', urgency: 'medium',
    chatThread: [
      { id: 'adc5-0', role: 'assistant', content: 'David Kim\'s lease at Oak Grove (Unit 3A) expires Sep 30. He\'s been a reliable tenant for 2 years. A renewal inquiry with a proposed 3% increase ($2,163/mo) has been sent.', timestamp: new Date('2026-03-09T09:55:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'context' },
      { id: 'adc5-2', role: 'user', content: 'Thanks for reaching out. The 3% increase seems a bit high — is there any flexibility?', timestamp: new Date('2026-03-10T14:00:00'), senderName: 'David Kim', senderType: 'tenant', messageType: 'message' },
      { id: 'adc5-3', role: 'assistant', content: 'David pushed back on 3%. I\'ve drafted a counter offering 2% with a 2-year term. Approve to send?', timestamp: new Date('2026-03-10T16:20:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'approval', draftReply: 'Hi David, thanks for your feedback. We value you as a tenant and want to find something that works for both of us. How about a 2% increase ($2,142/mo) if you sign a 2-year renewal? This locks in a great rate well below market. Let me know your thoughts!', approvalStatus: 'pending' },
    ],
  },
  {
    id: 'ad6', title: 'Fire extinguisher inspection — Maple Heights', mode: 'autonomous', status: 'active',
    participants: [{ type: 'agent', name: 'RentMate AI' }, { type: 'vendor', name: 'SafeGuard Fire Services' }],
    lastMessage: 'Inspection booked for March 14. Vendor confirmed.', lastMessageBy: 'RentMate AI', lastMessageAt: new Date('2026-03-11T08:00:00'),
    unreadCount: 0, propertyId: 'p4', category: 'compliance', urgency: 'high',
    chatThread: [
      { id: 'adc6-0', role: 'assistant', content: 'Annual fire extinguisher inspection at Maple Heights is 11 days overdue (was due Feb 28). This is a compliance risk. Contacting SafeGuard Fire Services to schedule ASAP.', timestamp: new Date('2026-03-11T06:55:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'context' },
      { id: 'adc6-2', role: 'user', content: 'We can do March 14 at 9am. $150 for all units.', timestamp: new Date('2026-03-11T07:45:00'), senderName: 'SafeGuard Fire Services', senderType: 'vendor', messageType: 'message' },
      { id: 'adc6-3', role: 'assistant', content: 'Booked inspection for March 14 at 9am. $150 confirmed. Added to compliance calendar.', timestamp: new Date('2026-03-11T08:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'internal' },
    ],
  },
  {
    id: 'ad7', title: 'AC repair — Unit A1, Riverside', mode: 'waiting_approval', status: 'active',
    participants: [{ type: 'agent', name: 'RentMate AI' }, { type: 'tenant', name: 'James Wu' }, { type: 'vendor', name: 'CoolAir HVAC' }],
    lastMessage: 'Vendor quote: $450 for compressor repair. Approve to proceed?', lastMessageBy: 'RentMate AI', lastMessageAt: new Date('2026-03-11T13:00:00'),
    unreadCount: 3, propertyId: 'p3', category: 'maintenance', urgency: 'high',
    chatThread: [
      { id: 'adc7-0', role: 'assistant', content: 'James Wu (Unit A1, Riverside Condos) reports his AC is blowing warm air. Possible refrigerant leak or compressor issue. CoolAir HVAC dispatched for diagnosis.', timestamp: new Date('2026-03-10T14:55:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'context' },
      { id: 'adc7-2', role: 'assistant', content: 'Possible refrigerant leak or compressor issue. Dispatched CoolAir HVAC for diagnosis.', timestamp: new Date('2026-03-10T15:30:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'internal' },
      { id: 'adc7-3', role: 'user', content: 'Diagnosed: compressor is failing. Repair cost $450, replacement $1,200. Recommend repair first.', timestamp: new Date('2026-03-11T12:00:00'), senderName: 'CoolAir HVAC', senderType: 'vendor', messageType: 'message' },
      { id: 'adc7-4', role: 'assistant', content: 'Vendor recommends compressor repair at $450 (vs $1,200 replacement). Approve repair to proceed?', timestamp: new Date('2026-03-11T13:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'approval', draftReply: 'Hi CoolAir HVAC, please proceed with the compressor repair at $450. We\'d like to get this resolved as soon as possible for the tenant. Please coordinate access with James Wu at Unit A1.', approvalStatus: 'pending' },
    ],
  },
  {
    id: 'ad8', title: 'Spring maintenance notice — All properties', mode: 'waiting_approval', status: 'active',
    participants: [{ type: 'agent', name: 'RentMate AI' }],
    lastMessage: 'Draft notice ready. Review before sending to all tenants.', lastMessageBy: 'RentMate AI', lastMessageAt: new Date('2026-03-10T10:00:00'),
    unreadCount: 1, propertyId: undefined, category: 'maintenance', urgency: 'low',
    chatThread: [
      { id: 'adc8-0', role: 'assistant', content: 'It\'s time to send the annual spring maintenance notice to all tenants across all properties. Covers pest control, AC check-ups, and landscaping schedules.', timestamp: new Date('2026-03-10T09:55:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'context' },
      { id: 'adc8-1', role: 'assistant', content: 'I\'ve drafted a spring maintenance notice covering pest control, AC check-ups, and landscaping. Ready for your review before sending to all tenants.', timestamp: new Date('2026-03-10T10:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'approval', draftReply: 'Dear Residents,\n\nSpring is here! Here are some upcoming maintenance activities:\n\n🐛 Pest Control — March 18-22 (schedule per building)\n❄️ AC Check-ups — Available by request, contact us to book\n🌿 Landscaping — Refreshed common areas starting March 25\n\nPlease ensure access to your unit on your scheduled pest control day. Reach out with any questions!\n\nBest,\nRentMate Property Management', approvalStatus: 'pending' },
    ],
  },
  {
    id: 'ad9', title: 'Lease renewal follow-up — Robert Taylor', mode: 'manual', status: 'paused',
    participants: [{ type: 'agent', name: 'RentMate AI' }, { type: 'tenant', name: 'Robert Taylor' }],
    lastMessage: 'No response after 2 follow-ups. Paused until manual intervention.', lastMessageBy: 'RentMate AI', lastMessageAt: new Date('2026-03-09T15:30:00'),
    unreadCount: 0, propertyId: 'p4', category: 'leasing', urgency: 'medium',
    chatThread: [
      { id: 'adc9-0', role: 'assistant', content: 'Robert Taylor\'s lease at Maple Heights (Unit 5C) expires June 30. A renewal offer was sent Feb 25 with no response. Follow-up sequence initiated.', timestamp: new Date('2026-03-01T09:55:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'context', relatedTasks: [{ taskId: 'ad1', label: 'Roof leak — Unit 5C' }] },
      { id: 'adc9-1', role: 'assistant', content: 'Sent renewal offer to Robert Taylor on Feb 25. No response yet.', timestamp: new Date('2026-03-01T10:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'internal' },
      { id: 'adc9-2', role: 'assistant', content: 'Follow-up sent March 5. Still no response.', timestamp: new Date('2026-03-05T10:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'internal' },
      { id: 'adc9-3', role: 'assistant', content: 'No response after 2 follow-ups. Pausing task — needs manual intervention.', timestamp: new Date('2026-03-09T15:30:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'approval', draftReply: 'Hi Robert, I wanted to follow up one more time regarding your lease renewal for Unit 5C. Your current lease expires June 30, and we\'d love to have you stay. Could you let us know your plans? Happy to discuss the terms if needed.', approvalStatus: 'pending' },
    ],
  },
  {
    id: 'ad10', title: 'Dishwasher repair — Unit 101', mode: 'autonomous', status: 'resolved',
    participants: [{ type: 'agent', name: 'RentMate AI' }, { type: 'tenant', name: 'Sarah Chen' }, { type: 'vendor', name: 'AppliancePro' }],
    lastMessage: 'Repair completed and confirmed by tenant. Task resolved.', lastMessageBy: 'RentMate AI', lastMessageAt: new Date('2026-03-09T12:00:00'),
    unreadCount: 0, propertyId: 'p1', category: 'maintenance', urgency: 'low',
    chatThread: [
      { id: 'adc10-0', role: 'assistant', content: 'Sarah Chen (Unit 101, Sunset Apartments) reported her dishwasher isn\'t draining after the cycle. AppliancePro has been scheduled for inspection.', timestamp: new Date('2026-03-06T07:55:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'context' },
      { id: 'adc10-2', role: 'assistant', content: 'Scheduled AppliancePro for inspection tomorrow.', timestamp: new Date('2026-03-06T09:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'internal' },
      { id: 'adc10-3', role: 'user', content: 'Drain hose was kinked. Fixed and tested — running fine now.', timestamp: new Date('2026-03-08T14:00:00'), senderName: 'AppliancePro', senderType: 'vendor', messageType: 'message' },
      { id: 'adc10-4', role: 'user', content: 'Works great now, thank you!', timestamp: new Date('2026-03-09T10:00:00'), senderName: 'Sarah Chen', senderType: 'tenant', messageType: 'message' },
      { id: 'adc10-5', role: 'assistant', content: 'Repair confirmed by tenant. Task resolved automatically.', timestamp: new Date('2026-03-09T12:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'internal' },
    ],
  },
  {
    id: 'ad11', title: 'Pest control scheduling — Riverside', mode: 'autonomous', status: 'cancelled',
    participants: [{ type: 'agent', name: 'RentMate AI' }, { type: 'vendor', name: 'GreenShield Pest Control' }],
    lastMessage: 'Cancelled — tenant reported issue resolved on its own.', lastMessageBy: 'RentMate AI', lastMessageAt: new Date('2026-03-07T14:00:00'),
    unreadCount: 0, propertyId: 'p3', category: 'maintenance', urgency: 'low',
    chatThread: [
      { id: 'adc11-0', role: 'assistant', content: 'James Wu (Unit A1, Riverside Condos) reported seeing ants in the kitchen. GreenShield Pest Control contacted for treatment scheduling.', timestamp: new Date('2026-03-05T08:55:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'context' },
      { id: 'adc11-2', role: 'user', content: 'We can schedule a treatment for March 10. $120 for the unit.', timestamp: new Date('2026-03-05T11:00:00'), senderName: 'GreenShield Pest Control', senderType: 'vendor', messageType: 'message' },
      { id: 'adc11-3', role: 'user', content: 'Actually, the ants seem to have gone away after I cleaned out the pantry. I don\'t think I need pest control anymore.', timestamp: new Date('2026-03-07T10:00:00'), senderName: 'James Wu', senderType: 'tenant', messageType: 'message' },
      { id: 'adc11-4', role: 'assistant', content: 'Tenant reports issue resolved. Cancelling vendor appointment and closing task.', timestamp: new Date('2026-03-07T14:00:00'), senderName: 'RentMate AI', senderType: 'ai', messageType: 'internal' },
    ],
  },
];

export const documentTypeLabels: Record<DocumentType, string> = {
  lease: 'Lease', invoice: 'Invoice', notice: 'Notice',
  inspection: 'Inspection', insurance: 'Insurance', other: 'Other',
};

export const mockDocuments: ManagedDocument[] = [
  {
    id: 'doc1', fileName: 'Lease_SunsetApt_Unit3B.pdf', fileType: 'application/pdf', fileSize: 245000,
    documentType: 'lease', status: 'ready', uploadedAt: new Date('2026-02-20T10:00:00'), analyzedAt: new Date('2026-02-20T10:02:00'),
    tags: [{ type: 'property', id: 'p1', label: 'Sunset Apartments' }, { type: 'tenant', id: 't2', label: 'Maria Garcia' }],
    aiSummary: '12-month residential lease for Unit 3B at Sunset Apartments. Tenant: Maria Garcia. Monthly rent: $2,400. Lease period: Jan 1 – Dec 31, 2026.',
    aiExtracted: { 'Monthly Rent': '$2,400', 'Lease Start': 'Jan 1, 2026', 'Lease End': 'Dec 31, 2026', 'Security Deposit': '$4,800' },
  },
  {
    id: 'doc2', fileName: 'Invoice_PlumbingRepair_Mar2026.pdf', fileType: 'application/pdf', fileSize: 89000,
    documentType: 'invoice', status: 'ready', uploadedAt: new Date('2026-03-08T14:30:00'), analyzedAt: new Date('2026-03-08T14:31:00'),
    tags: [{ type: 'property', id: 'p1', label: 'Sunset Apartments' }],
    aiSummary: 'Plumbing repair invoice from QuickFix Plumbing. Emergency pipe repair in Unit 5A. Total: $450.',
    aiExtracted: { 'Vendor': 'QuickFix Plumbing', 'Amount': '$450', 'Date': 'Mar 8, 2026', 'Service': 'Emergency pipe repair' },
    actionDeskTaskId: 'ad1',
  },
  {
    id: 'doc3', fileName: 'Fire_Inspection_OakGrove.pdf', fileType: 'application/pdf', fileSize: 512000,
    documentType: 'inspection', status: 'analyzing', uploadedAt: new Date('2026-03-11T09:00:00'),
    tags: [],
    aiSummary: undefined,
  },
  {
    id: 'doc4', fileName: 'Insurance_Certificate_2026.pdf', fileType: 'application/pdf', fileSize: 178000,
    documentType: 'insurance', status: 'ready', uploadedAt: new Date('2026-01-15T11:20:00'), analyzedAt: new Date('2026-01-15T11:22:00'),
    tags: [{ type: 'property', id: 'p3', label: 'Riverside Condos' }],
    aiSummary: 'Liability insurance certificate for Riverside Condos. Coverage: $2M. Policy period: 2026 full year.',
    aiExtracted: { 'Provider': 'StateFarm', 'Coverage': '$2,000,000', 'Expires': 'Dec 31, 2026' },
  },
];
