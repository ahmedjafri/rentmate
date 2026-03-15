# Automation & Trigger Design

## 1. Overview
Automations in RentMate are the proactive engine of the platform. While the **Action Desk** is where work is managed, **Automations** are where work is identified and initiated. This document describes the evolution of the automation engine from simple interval-based checks to a robust, multi-trigger system powered by the **Property-Flow DSL**.

## 2. Trigger Types
The current implementation relies on a simple `interval_hours` check. To support complex property management workflows (like seasonal maintenance or event-driven compliance), the trigger system is expanding.

### A. Schedule Triggers (Cron & Intervals)
For tasks that happen at regular intervals.
- **Interval:** `every 24 hours`, `every 7 days`.
- **Cron-like:** `1st of every month`, `every Monday at 9am`.
- **Annual/Seasonal:** Specific months or dates.

**Example: Gutter Cleaning (Seasonal)**
```yaml
trigger:
  type: schedule
  months: [4, 10] # April and October
  day_of_month: 15
  time: "09:00"
```

### B. Event Triggers (Reactive)
For tasks triggered by changes in the system state.
- `on_lease_signed`
- `on_tenant_move_in` / `on_tenant_move_out`
- `on_payment_received` / `on_payment_failed`
- `on_message_received`

**Example: Welcome Packet**
```yaml
trigger:
  type: event
  event_name: "on_lease_signed"
scope:
  resource: lease
actions:
  - type: create_task
    subject: "Send Welcome Packet to {{lease.tenant.first_name}}"
    category: leasing
```

### C. Context-Driven Triggers (Inference-Based)
For tasks triggered by new knowledge or data extracted from unstructured sources (documents, photos, conversation transcripts). These triggers rely on the agent's ability to process context and flag relevant findings.
- `on_document_processed`
- `on_inspection_uploaded`
- `on_chat_finding_detected`
- `on_context_updated`

**Example: Roof Condition Alert (from Inspection Report)**
```yaml
trigger:
  type: context
  event_name: "on_document_processed"
# ... (rest of example)
```

**Example: Lease Violation Alert (from Tenant Chat)**
If a tenant mentions an unauthorized pet during a routine maintenance chat, the AI extracts this "finding" into the unit's context.
```yaml
trigger:
  type: context
  event_name: "on_chat_finding_detected"
scope:
  resource: unit
conditions:
  - field: "ai.last_finding.category"
    operator: "equals"
    value: "lease_violation"
  - field: "ai.last_finding.description"
    operator: "contains"
    value: "pet"
actions:
  - type: create_task
    subject: "Potential Lease Violation: Pet at {{unit.label}}"
    category: compliance
    body: "During a chat, the AI detected a mention of a pet at {{unit.label}}. Review the transcript and confirm."
```
conditions:
  - field: "document.type"
    operator: "equals"
    value: "inspection_report"
  - field: "document.extracted_data.findings"
    operator: "contains"
    value: "roof replacement"
actions:
  - type: create_task
    subject: "Urgent: Roof Replacement Recommended"
    category: maintenance
    urgency: high
    body: "Inspection report for {{document.property.address_line1}} suggests roof is near end-of-life."
```

### D. State-Watch Triggers (Continuous)
For conditions that become true over time (e.g., a lease becomes "expiring"). These are technically scheduled checks that look for a specific state change.

## 3. Creating Automations
Automations can be created in three ways:

1.  **Library Templates:** One-click activation of common tasks (Late Rent, Expiring Leases).
2.  **Natural Language (AI-Assisted):** User tells the agent: "Remind me to check the smoke detectors every January." The agent translates this into Property-Flow DSL.
3.  **DSL Editor:** For power users to manually define scopes, conditions, and actions.

## 4. Automation vs. Autonomy
It is critical to distinguish between the *trigger* of an action and the *execution* of an action.

- **Automation:** Defines *when* and *why* a task is identified (The Trigger).
- **Autonomy Policy:** Defines *how* the resulting action is executed (The Mode).

When an automation triggers a `create_task` action, the system checks the **Autonomy Framework** to decide if it should be `Draft & Wait`, `Auto-Execute`, etc.

## 5. Temporal & Seasonal Logic
Property management is highly seasonal. The automation engine supports temporal constraints:

- **Seasons:** `Spring`, `Summer`, `Fall`, `Winter` (mapped to regional months).
- **Lease Relative:** `30 days before lease.end_date`.
- **Move Relative:** `2 weeks after move_in_date`.

### Case Study: Winterization
A user wants to ensure all properties are winterized.
```yaml
name: "Winterization Prep"
trigger:
  type: schedule
  months: [11] # November
  day_of_month: 1
actions:
  - type: create_task
    subject: "Winterize Property: {{property.address_line1}}"
    category: maintenance
    body: "Time for annual furnace inspection and pipe insulation check."
```

## 6. Simulation & Dry Runs
Before activating a new automation, users can "Simulate" the trigger. The system runs the DSL against current data and shows exactly which tasks *would* have been created in the Action Desk. This builds trust and ensures the "Scope" and "Filters" are correctly configured.
