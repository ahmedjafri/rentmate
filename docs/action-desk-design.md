# Action Desk & Task Chat Design

## 1. Overview
The **Action Desk** is the central command center for the RentMate user. It aggregates all ongoing tasks, suggestions, and conversations into a single prioritized list. The **Task Chat** is the primary interface for interacting with these tasks, providing a unified experience for both strategic planning with the AI and operational communication with tenants and vendors.

## 2. The Action Desk
The Action Desk serves as a "To-Do" list for property management. 

### Task List & Filtering
Tasks are categorized by their nature and required level of human intervention:
- **Active Tasks:** Ongoing items requiring attention or monitoring.
- **Needs Approval:** Tasks staged by the AI (per the Autonomy Framework) that require user confirmation before execution.
- **Autonomous:** Tasks currently being handled by the AI without immediate human intervention.
- **Completed/Archived:** History of resolved items.

### Task Metadata
Each task in the Action Desk contains:
- **Category:** Rent, Maintenance, Leasing, Compliance.
- **Urgency:** Low, Medium, High, Critical.
- **Mode:** Manual, Suggest & Wait, Review Window, Fully Autonomous.
- **Context:** Linked Property, Unit, Lease, or Document.
- **Participants:** Who is involved in this specific task thread.

## 3. Task Chat Architecture
RentMate distinguishes between two primary communication flows, though both utilize the same underlying `Conversation` and `Message` models.

### A. Internal Chat: User ↔ RentMate Agent
*Strategic & Command Flow*
- **Purpose:** The user manages the agent, asks questions about the portfolio, sets policies, or gives direct commands.
- **Agent Role:** Strategic Advisor.
- **Key Capabilities:** 
    - Querying data ("Which leases are expiring next month?").
    - Planning actions ("Draft a renewal offer for Unit 3A with a 5% increase").
    - Spawning external tasks ("Tell the tenant in 4B their maintenance request is approved").
- **Privacy:** These conversations are strictly internal and never visible to tenants or vendors.

### B. External Chat: RentMate Agent ↔ Tenant/Vendor
*Operational & Execution Flow*
- **Purpose:** The agent carries out management tasks: triaging maintenance, collecting rent, or screening applicants.
- **Agent Role:** Virtual Property Manager (Representative).
- **Key Capabilities:**
    - Collecting info ("Can you send a photo of the leak?").
    - Coordinating ("The plumber is available Tuesday at 2pm, does that work?").
    - Negotiating ("The owner is willing to accept a 4% increase instead of 5%").
- **Visibility:** The User can monitor these chats in real-time but typically only intervenes when the AI reaches an autonomy ceiling or confidence threshold.

## 4. Cross-Chat Integration
The true power of RentMate lies in how these two flows interact.

### Internal Command → External Action
When a user gives a command in the Internal Chat, the agent may spawn or update an External Chat:
1. **User (Internal):** "Approve the plumber for 220 Elm."
2. **RentMate (System):** Creates/Updates a Task for 220 Elm.
3. **RentMate (External):** Messages the Plumber: "You are approved for the repair at 220 Elm. Please schedule with the tenant."

### External Event → Internal Suggestion
When an external event occurs, the agent notifies the user via the Action Desk:
1. **Tenant (External):** "My heater is making a weird noise."
2. **RentMate (AI):** Triages the message, identifies it as a maintenance issue.
3. **RentMate (Action Desk):** Creates a task "Noisy Heater at 104 Oak St" with a suggested response or vendor dispatch.
4. **User (Internal):** Reviews the task and clicks "Approve" or chats with RentMate to refine the plan.

## 5. Message Types & Transparency
To maintain clarity for the user, messages within a task are flagged:
- **Message:** Standard communication between participants.
- **Internal Note:** Private thoughts or logs added by the AI or User.
- **Approval Request:** A specific staged action waiting for a "Yes/No" from the user.
- **System Event:** Automated logs (e.g., "AI dispatched Plumber Joe").

## 6. Technical Implementation Details

### Unified Conversation Model
The `Conversation` model (see `db/models.py`) acts as the "Task" container. 
- `is_task = True` identifies it as an Action Desk item.
- `confidential = True` (or a specific `ConversationType`) distinguishes Internal vs. External threads.

### Participant Routing
- **Internal:** Participants = `[AccountUser, RentMateAgent]`.
- **External:** Participants = `[RentMateAgent, Tenant/Vendor]`.
- **Hybrid:** In some cases, a user might "Join" an external chat to speak directly to a tenant, shifting the agent to an "Observer/Advisor" role.

### Autonomous Task Spawning
The agent can autonomously create tasks based on:
1. **Scheduled Checks:** (e.g., "Lease Expiring" check creates a renewal task).
2. **Inbound Messages:** (e.g., SMS from tenant creates a maintenance task).
3. **User Commands:** (e.g., "Remind everyone about the water shutoff").
