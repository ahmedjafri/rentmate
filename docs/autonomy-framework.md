# RentMate Autonomy Preferences Framework

## 1. Core Philosophy: Policy-Driven Autonomy
Instead of relying on rigid, category-wide toggles, RentMate uses a **Policy-Driven Autonomy** model. Users define *Autonomy Policies* that act as guardrails for the AI. By default, RentMate ships with a strict "Human-in-the-loop" policy for everything. Users selectively grant autonomy by adding or modifying policies.

## 2. The Autonomy Modes
Every policy targets one of four execution modes:
1. **Notify Only:** The AI observes an event, logs it, and alerts the user without taking action or drafting a response.
2. **Draft & Wait (Default):** The AI prepares an action (e.g., drafts an email, stages a vendor request) and places it in the Action Desk for human approval.
3. **Review Window:** The AI drafts the action and schedules it to execute after a countdown (e.g., 24 hours). The user can veto or edit during this window.
4. **Auto-Execute:** The AI executes the action immediately and logs the result for later review.

## 3. Structure of an Autonomy Policy
An Autonomy Policy is evaluated before the AI takes any action. It consists of:
- **Scope:** The category or specific event type (e.g., `Maintenance`, `Lease Renewal`, `Tenant Message`).
- **Mode:** The chosen Autonomy Mode (e.g., `Auto-Execute`).
- **Constraints (The Guardrails):** A set of logical conditions that *must* be true for the mode to apply. If any constraint fails, the system falls back to a safer mode.
- **Fallback:** The mode to use if constraints fail (typically `Draft & Wait`).

### Example Policies:
* **"Auto-dispatch maintenance for cheap fixes"**
  * Scope: `Maintenance Request`
  * Mode: `Auto-Execute`
  * Constraints: `estimated_cost <= $200` AND `confidence_score >= 0.90`
  * Fallback: `Draft & Wait`

* **"Wait 24 hours before sending late rent notices"**
  * Scope: `Late Rent Notice`
  * Mode: `Review Window (24h)`
  * Constraints: `days_late >= 5`
  * Fallback: `Notify Only`

## 4. System Ceilings (Hardcoded Guardrails)
To protect users from catastrophic errors, RentMate enforces maximum autonomy limits on certain sensitive actions. User policies cannot exceed these ceilings:
- **Evictions & Legal Action:** Ceiling = *Draft & Wait*
- **Lease Termination:** Ceiling = *Draft & Wait*
- **Deposit Deductions:** Ceiling = *Draft & Wait*
- **Routine Maintenance:** Ceiling = *Auto-Execute*

## 5. Confidence Degradation
The LLM backing RentMate generates a internal **Confidence Score** (0.0 to 1.0) for its proposed actions based on context clarity and ambiguity. 
- All `Auto-Execute` policies require a minimum confidence score (e.g., `0.85`).
- If the AI is unsure (e.g., a tenant sends a highly confusing, emotional message regarding rent), the confidence score drops, automatically failing the constraint and triggering the safer Fallback mode.

## 6. Granular Overrides (Entity-Level Policies)
Policies can be scoped not just globally, but to specific entities:
- **Property Overrides:** An out-of-state property might have higher maintenance autonomy than a property the user lives next door to.
- **Tenant Overrides:** A VIP tenant or a historically difficult tenant can have a strict `Draft & Wait` override for all communications to ensure a human always reviews the messaging.

## 7. Data Model Concept
Policies integrate naturally with RentMate's existing `Property-Flow` DSL, representing autonomy rules as deterministic YAML/JSON configurations:

```yaml
name: "Low-Cost Maintenance Auto-Dispatch"
scope: "action.maintenance.dispatch"
mode: "auto_execute"
constraints:
  - field: "action.estimated_cost"
    operator: "lte"
    value: 200
  - field: "ai.confidence_score"
    operator: "gte"
    value: 0.9
fallback_mode: "draft_and_wait"
```
