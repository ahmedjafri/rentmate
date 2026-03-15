Property-Flow DSL (v1.1)
========================

1\. Overview
------------

**Property-Flow** is a YAML-based declarative language for automating property management tasks. It acts as the intermediate layer between a user's intent and the system's execution engine.

### Design Goals

-   **Safety:** No arbitrary code execution; limited to predefined actions and scopes.
-   **Determinism:** Given the same data and script, the outcome is always predictable.
-   **LLM-Friendly:** Structured, schema-validated format that LLMs can generate accurately.
-   **Auditability:** Humans can read a script and immediately understand its logic.

* * * * *

2\. Architecture
----------------

The automation follows a **Translate-Validate-Execute** pipeline:

1.  **Input:** User provides a natural language command.
2.  **Translation:** An LLM converts the command into Property-Flow YAML.
3.  **Validation:** The system checks the YAML against a JSON Schema.
4.  **Execution:** The Runner iterates through the **Scope**, evaluates **Conditions**, and performs **Actions**.

Deduplication is handled automatically by the Runner: if an open task with the same subject + property/unit already exists, no new task is created.

* * * * *

3\. Schema Specification
------------------------

### 3.1 Root Attributes

| **Field** | **Type** | **Description** |
| --- | --- | --- |
| `name` | String | Short descriptive name for the automation. |
| `description` | String | Summary of what the automation does. |
| `trigger` | Object | Defines when the script runs. |
| `scope` | Object | Defines the dataset the script iterates over. |
| `conditions` | Array | Per-record logic gates that must pass for actions to trigger. |
| `actions` | Array | Side effects to perform for each matching record. |

### 3.2 Triggers

The trigger system defines *when* the automation script runs. For a detailed design of all trigger types, see [Automation & Trigger Design](./automation-design.md).

```yaml
trigger:
  type: schedule
  interval_hours: 24        # how often to run (legacy interval)
  # or
  months: [4, 10]           # specific months for seasonal tasks
  day_of_month: 15
```

Supported types: `schedule` (Interval, Cron, Seasonal), `event` (Lease, Payment), and `context` (Document Processing).

### 3.3 Scope & Filters

The `scope` defines the root resource to iterate over. `filters` are evaluated at the database level (SQL WHERE) before per-record conditions are applied.

**Supported resources:**

| Resource | Description | Key fields |
| --- | --- | --- |
| `property` | A managed property | `address_line1`, `city`, `state`, `postal_code` |
| `unit` | A rentable unit within a property | `label`, `property_id`, `active_lease_count`, `days_vacant` |
| `lease` | A lease agreement | `start_date`, `end_date`, `rent_amount`, `payment_status`, `days_until_end` |
| `tenant` | A tenant / contact | `first_name`, `last_name`, `email`, `phone` |
| `document` | An uploaded file | `filename`, `type`, `extracted_data`, `status` |
| `finding` | A specific piece of context identified by AI | `category`, `description`, `source_type`, `confidence` |

Related fields are accessed via dot notation: `lease.tenant.first_name`, `unit.property.address_line1`, `document.extracted_data.findings`, `ai.last_finding.description`.

**Computed / virtual fields:**

| Field | Available on | Description |
| --- | --- | --- |
| `active_lease_count` | `unit` | Number of leases with `end_date >= today` |
| `days_vacant` | `unit` | Days since the last lease ended (0 if never leased) |
| `days_until_end` | `lease` | Days until `end_date` |

### 3.4 Conditions

`conditions` is a list of rules evaluated **in memory** per record (after DB-level filters). All rules in the list must pass (implicit AND). Use `any_of` for OR logic:

```yaml
conditions:
  - field: active_lease_count
    operator: equals
    value: 0

  - any_of:
    - field: city
      operator: not_exists
    - field: state
      operator: not_exists
```

### 3.5 Operators

| Operator | Description |
| --- | --- |
| `equals` | Exact match |
| `not_equals` | Not equal |
| `gt` / `lt` | Greater / less than |
| `gte` / `lte` | Greater or equal / less or equal |
| `in` | Value is in a list: `value: [late, overdue]` |
| `exists` | Field is not null / not empty |
| `not_exists` | Field is null or empty |
| `contains` | String or array contains value |

### 3.6 Actions

#### `create_task` *(implemented)*

Creates a suggested task in the Action Desk.

| Field | Type | Description |
| --- | --- | --- |
| `subject` | String (template) | Task title |
| `category` | `rent` \| `leasing` \| `compliance` \| `maintenance` | Task category |
| `urgency` | `low` \| `medium` \| `high` \| `critical` | Task urgency |
| `body` | String (template) | Context message shown in the task |

Urgency may be a conditional expression:

```yaml
urgency: >
  high   if days_until_end <= 30
  medium otherwise
```

#### `send_notification` *(planned)*

Sends an SMS or email to a recipient.

#### `update_record` *(planned)*

Updates a field on the matched record.

### 3.7 Variable Injection

Templates use `{{variable}}` syntax. Available variables depend on the scope resource:

**Global:** `{{today}}`

**Scope variables:**
- `{{unit.label}}`, `{{unit.property.address_line1}}`
- `{{lease.end_date}}`, `{{lease.rent_amount}}`, `{{lease.tenant.first_name}}`
- `{{tenant.first_name}}`, `{{tenant.last_name}}`
- `{{property.address_line1}}`, `{{property.city}}`

**Computed variables:** `{{days_vacant}}`, `{{days_until_end}}`, `{{active_lease_count}}`

**Check parameters:** `{{params.warn_days}}`, `{{params.min_vacancy_days}}`, `{{params.interval_hours}}`

* * * * *

4\. Security & Guardrails
--------------------------

1.  **Dry Run Mode:** The Runner supports a `dry_run` flag that previews tasks without saving them (used by the Simulate feature).
2.  **Schema Validation:** All scripts must pass JSON Schema validation before being saved.
3.  **Rate Limiting:** `send_notification` actions will be rate-limited per resource to prevent spamming tenants.
4.  **No Arbitrary Code:** All field references, operators, and action types are drawn from a fixed allowlist.

* * * * *

5\. Built-in Check Scripts
---------------------------

All six built-in checks are defined in Property-Flow DSL. Their scripts are viewable in the Automations detail page.

### vacant_units

```yaml
name: "Vacant Units"
description: >
  Creates a leasing task for every unit that has no active lease.
  Skips units vacant for fewer than min_vacancy_days days.

trigger:
  type: schedule
  interval_hours: "{{params.interval_hours}}"

scope:
  resource: unit

conditions:
  - field: active_lease_count
    operator: equals
    value: 0
  - field: days_vacant
    operator: gte
    value: "{{params.min_vacancy_days}}"

actions:
  - type: create_task
    subject: "Vacant unit: {{unit.label}} at {{unit.property.address_line1}}"
    category: leasing
    urgency: >
      high   if days_vacant > 60
      medium if days_vacant > 14
      low    otherwise
    body: >
      Unit "{{unit.label}}" at {{unit.property.address_line1}} has been vacant
      for {{days_vacant}} day(s) (last leased to {{unit.last_lease.tenant.first_name}}
      {{unit.last_lease.tenant.last_name}}, ended {{unit.last_lease.end_date}}).
      Previous rent: ${{unit.last_lease.rent_amount}}/month.
```

### expiring_leases

```yaml
name: "Expiring Leases"
description: >
  Creates a leasing task for every active lease whose end date falls
  within the next warn_days days.

trigger:
  type: schedule
  interval_hours: "{{params.interval_hours}}"

scope:
  resource: lease
  filters:
    - field: end_date
      operator: gte
      value: "{{today}}"
    - field: end_date
      operator: lte
      value: "{{today + params.warn_days}}"

actions:
  - type: create_task
    subject: "Lease expiring {{lease.end_date}}: {{lease.tenant.first_name}} {{lease.tenant.last_name}} – {{lease.unit.label}}"
    category: leasing
    urgency: >
      high   if days_until_end <= 30
      medium otherwise
    body: >
      Lease for {{lease.tenant.first_name}} {{lease.tenant.last_name}} at
      {{lease.unit.label}}, {{lease.property.address_line1}} expires on
      {{lease.end_date}} ({{days_until_end}} days).
      Reach out about renewal or move-out logistics.
```

### overdue_rent

```yaml
name: "Overdue Rent"
description: >
  Creates a high-urgency rent task for every lease whose payment
  status is late or overdue.

trigger:
  type: schedule
  interval_hours: "{{params.interval_hours}}"

scope:
  resource: lease
  filters:
    - field: payment_status
      operator: in
      value: [late, overdue]

actions:
  - type: create_task
    subject: "Overdue rent: {{lease.tenant.first_name}} {{lease.tenant.last_name}} – {{lease.unit.label}}"
    category: rent
    urgency: high
    body: >
      {{lease.tenant.first_name}} {{lease.tenant.last_name}} at
      {{lease.unit.label}}, {{lease.property.address_line1}} has payment
      status "{{lease.payment_status}}" (${{lease.rent_amount}}/month).
      Follow up on outstanding rent.
```

### incomplete_properties

```yaml
name: "Incomplete Properties"
description: >
  Creates a compliance task for every property missing city,
  state, or postal code.

trigger:
  type: schedule
  interval_hours: "{{params.interval_hours}}"

scope:
  resource: property

conditions:
  - any_of:
    - field: city
      operator: not_exists
    - field: state
      operator: not_exists
    - field: postal_code
      operator: not_exists

actions:
  - type: create_task
    subject: "Incomplete address: {{property.address_line1}}"
    category: compliance
    urgency: low
    body: >
      Property "{{property.address_line1}}" is missing one or more address
      fields (city, state, postal code). Complete the address so leases and
      documents are accurate.
```

### missing_contact

```yaml
name: "Missing Contact Info"
description: >
  Creates a compliance task for every tenant with neither a phone
  number nor an email address on file.

trigger:
  type: schedule
  interval_hours: "{{params.interval_hours}}"

scope:
  resource: tenant

conditions:
  - field: phone
    operator: not_exists
  - field: email
    operator: not_exists

actions:
  - type: create_task
    subject: "Missing contact info: {{tenant.first_name}} {{tenant.last_name}}"
    category: compliance
    urgency: low
    body: >
      Tenant "{{tenant.first_name}} {{tenant.last_name}}" has no phone number
      or email on file. Add contact details so they can be reached.
```

### expired_leases

```yaml
name: "Expired Leases"
description: >
  Creates a leasing task for every lease that ended before today
  and whose unit has no newer active lease to replace it.

trigger:
  type: schedule
  interval_hours: "{{params.interval_hours}}"

scope:
  resource: lease
  filters:
    - field: end_date
      operator: lt
      value: "{{today}}"

conditions:
  - field: unit.active_lease_count
    operator: equals
    value: 0

actions:
  - type: create_task
    subject: "Expired lease: {{lease.tenant.first_name}} {{lease.tenant.last_name}} – {{lease.unit.label}}"
    category: leasing
    urgency: high
    body: >
      The lease for {{lease.tenant.first_name}} {{lease.tenant.last_name}} at
      {{lease.unit.label}}, {{lease.property.address_line1}} expired on
      {{lease.end_date}} and has not been renewed.
      Confirm move-out status or start a renewal conversation.
```
