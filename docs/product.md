# RentMate: The Virtual Property Manager

**RentMate** is an AI-driven, fully capable virtual property manager designed to automate the heavy lifting of real estate management. Your AI manager handles tenant communication, screens applicants, coordinates maintenance, and keeps your books in order — so you can own property without it owning your time.

---

## Why Now

Managing rental property has always been time-consuming, but the real cost is no longer obvious. Tenants expect instant responses. Maintenance issues escalate when ignored. Late rent compounds. Compliance requirements vary by jurisdiction and change without warning.

Traditional property management software gives you better spreadsheets. It still requires *you* to do the work — reading every message, making every call, drafting every notice.

AI changes that. For the first time, a piece of software can actually *act* on your behalf: respond to a tenant inquiry at 2am, triage a maintenance request, follow up on a late payment, or draft a lease renewal offer — in your voice, within the boundaries you set. RentMate is built around that premise from the ground up.

---

## 🏠 Open Source Version (Available Now)

The Open Source version of RentMate is designed for individual landlords and developers who want full control over their data and infrastructure. It includes the core engine required to run a complete property management stack.

### Key Features

* **Customizable AI Agent:** The heart of RentMate. Configure your virtual manager's tone, strictness, and decision-making authority to match your management style. Every other feature flows through it.

* **Tenant Communication Hub:** The AI handles inbound messages from tenants across channels — answering routine questions, escalating urgent issues, and keeping a full conversation history so nothing falls through the cracks. (Note: See [Action Desk & Task Chat Design](./action-desk-design.md))

* **Leasing Automation:** AI-assisted screening, application tracking, and digital lease generation. (Note: See [Automation & Trigger Design](./automation-design.md) for how these workflows are scheduled.)

* **Maintenance Ticketing:** Tenants report issues, the AI triages urgency, notifies the right vendor, and follows up — without you picking up the phone. Full history per unit. (Note: Includes seasonal maintenance scheduling.)

* **Rent Collection Tracking:** Automated rent reminders and escalating late notices sent in your name, calibrated to your tone settings. Formal notices generated automatically when needed. Integration with payment gateways to log incoming payments.

* **Document Management:** Secure storage for leases, inspection photos, and compliance documents — organized by property and tenant.

* **Tenant Self-Service Portal:** Tenants can view their lease, check payment status, and submit maintenance requests directly — reducing inbound volume without reducing service quality.

* **Lease Renewal Pipeline:** 90/60/30-day automated outreach to tenants with expiring leases. The AI negotiates within the parameters you define — acceptable rent increase range, preferred terms — and surfaces exceptions for your review.

* **Vendor / Contractor Network:** Maintain a directory of trusted vendors per property. The AI routes maintenance tickets based on issue type and vendor availability, and tracks resolution.

* **Move-In / Move-Out Workflows:** Guided inspection checklists, automated scheduling, AI-generated condition reports from photos, and security deposit tracking.

* **Financial Reporting:** Per-property income and expense summaries, rent roll, and exportable reports for tax season. A clear ledger without the complexity of full accounting software.

* **Compliance Alerts:** Jurisdiction-aware notifications for lease notice requirements, habitability standards, required inspections, and rent control rules. Know what's required before it becomes a liability.

* **Multi-Manager Teams:** Role-based access so a property owner can delegate specific properties or units to a manager without exposing the full portfolio.

---

## 🤖 AI Suggestions & Autonomy Levels

RentMate's AI agent operates through a suggestion system. By default, every suggestion requires human-in-the-loop (HITL) approval. Owners and managers can tune autonomy per category based on their comfort level.

*(Note: See the [RentMate Autonomy Preferences Framework](./autonomy-framework.md) for the detailed, policy-driven architecture that powers these settings.)*

### Autonomy Levels

* **Notify Only** — AI informs the owner, takes no action.
* **Suggest & Wait** — AI drafts an action, waits for approval. *(This is the default for all suggestions.)*
* **Auto with Review Window** — AI acts after a configurable delay (e.g., 24 hours) unless vetoed.
* **Fully Autonomous** — AI acts immediately and logs the action.

Each suggestion type has a **maximum autonomy ceiling** — some actions can never be made fully autonomous regardless of owner preference.

### Design Principles

* **Default: Suggest & Wait for everything.** Owners opt *in* to higher autonomy per category.
* **Autonomy ceiling per category** — some actions (legal notices, tenant rejection, deposit deductions) should never be fully autonomous.
* **Dollar threshold gates** — e.g., auto-approve repairs under $200, require approval above.
* **Escalation on uncertainty** — if the AI's confidence is low, it drops to Notify Only regardless of configured autonomy.
* **Full audit log** — every suggestion, whether acted on or not, is logged with timestamp, action taken, and who approved it.

---

### Rent & Payments

| Suggestion | Example | Max Autonomy |
|---|---|---|
| Late rent reminder | "Tenant in 4B is 3 days late — send friendly reminder?" | Fully Autonomous |
| Escalating late notice | "10 days late — send formal notice per [jurisdiction] rules?" | Auto with Review Window |
| Late fee application | "Apply $50 late fee per lease terms?" | Auto with Review Window |
| Payment plan offer | "Tenant requested hardship plan — here's a 3-month proposal" | Suggest & Wait |
| Rent increase recommendation | "Unit 2A is $200 below market — suggest 5% increase at renewal" | Notify Only |
| Security deposit deduction | "Deduct $340 for wall damage based on move-out inspection" | Suggest & Wait |
| Partial payment handling | "Tenant sent $800 of $1,200 — accept or reject per your policy?" | Suggest & Wait |

---

### Maintenance & Property

| Suggestion | Example | Max Autonomy |
|---|---|---|
| Urgent repair dispatch | "Burst pipe in 3A — dispatch preferred plumber now?" | Fully Autonomous |
| Routine repair routing | "Broken cabinet hinge — schedule handyman this week?" | Auto with Review Window |
| Preventive maintenance scheduling | "Schedule annual gutter cleaning for October?" | Auto with Review Window |
| Seasonal prep reminders | "Winterization due — furnace inspections for all units?" | Suggest & Wait |
| Vendor follow-up | "Electrician hasn't confirmed Thursday appointment — send follow-up?" | Fully Autonomous |
| Repair cost approval | "Plumber quotes $1,800 for water heater replacement — approve?" | Suggest & Wait |
| Warranty claim suggestion | "Appliance is 2 years old, still under warranty — file claim instead of paying out of pocket" | Notify Only |
| Recurring issue escalation | "3rd plumbing call for unit 7 in 6 months — recommend full line inspection" | Notify Only |

---

### Tenant Communication

| Suggestion | Example | Max Autonomy |
|---|---|---|
| Routine inquiry response | "Tenant asked about guest parking policy — draft reply from lease terms" | Fully Autonomous |
| Noise/neighbor complaint acknowledgment | "Acknowledge complaint, remind both parties of quiet hours?" | Auto with Review Window |
| Emergency communication | "Water shutoff in building — notify all tenants now?" | Fully Autonomous |
| Lease clause clarification | "Tenant asked about subletting — respond with lease section 12 language" | Auto with Review Window |
| Sensitive/legal matter escalation | "Tenant mentioned lawyer — flag for your direct response" | Notify Only |
| Satisfaction check-in | "Tenant moved in 30 days ago — send check-in message?" | Fully Autonomous |

---

### Leasing & Turnover

| Suggestion | Example | Max Autonomy |
|---|---|---|
| Application follow-up | "Applicant hasn't submitted pay stubs — send reminder?" | Fully Autonomous |
| Screening summary | "Applicant scores 82/100 — credit good, income 3.1x rent, no evictions" | Notify Only |
| Applicant rejection/approval | "Recommend approving/denying based on your criteria" | Suggest & Wait |
| Listing price suggestion | "Based on comps, list unit 5C at $1,450" | Suggest & Wait |
| Showing scheduling | "3 applicants want to view unit — propose Saturday 10am–12pm block?" | Auto with Review Window |
| Vacancy marketing boost | "Unit listed 21 days with low interest — suggest price drop or new photos" | Notify Only |

---

### Lease Lifecycle

| Suggestion | Example | Max Autonomy |
|---|---|---|
| 90-day renewal outreach | "Lease expires Dec 1 — send renewal inquiry now?" | Auto with Review Window |
| Renewal offer generation | "Offer 12-month renewal at 3% increase (within your 2–5% range)" | Suggest & Wait |
| Month-to-month conversion notice | "Tenant didn't respond to renewal — notify of MTM conversion + new rate?" | Suggest & Wait |
| Lease violation notice | "Unauthorized pet observed in inspection photos — send notice?" | Suggest & Wait |
| Non-renewal / move-out notice | "Send required 60-day non-renewal notice per [state] law?" | Notify Only |

---

### Move-In / Move-Out

| Suggestion | Example | Max Autonomy |
|---|---|---|
| Inspection scheduling | "Move-out date March 31 — schedule walkthrough for April 1?" | Auto with Review Window |
| Condition report generation | "AI-generated report from 42 photos — review before sending to tenant" | Suggest & Wait |
| Turnover vendor coordination | "Unit needs paint + deep clean — schedule vendors for April 2–5?" | Suggest & Wait |
| Key exchange reminder | "Remind tenant to return keys by move-out date?" | Fully Autonomous |
| Deposit return calculation | "Deductions total $275 — return $725 within 21-day window?" | Suggest & Wait |

---

### Financial & Tax

| Suggestion | Example | Max Autonomy |
|---|---|---|
| Expense categorization | "Classify $180 Home Depot charge as maintenance/repairs?" | Auto with Review Window |
| Monthly P&L summary | "Net income across portfolio: $4,200 — 3 units positive, 1 negative" | Fully Autonomous |
| Tax document reminder | "1099s due Jan 31 — 2 vendors need W-9s on file" | Notify Only |
| Insurance renewal reminder | "Policy on 142 Oak St expires in 45 days — contact agent?" | Notify Only |
| Unusual expense flag | "Maintenance spend for unit 8 is 3x the 12-month average" | Notify Only |

---

### Compliance & Legal

| Suggestion | Example | Max Autonomy |
|---|---|---|
| Required notice timing | "State requires 30-day notice for rent increase — send by March 1" | Notify Only |
| Habitability check reminder | "Annual smoke detector inspection due — schedule?" | Suggest & Wait |
| Rent control cap alert | "Local rent control limits increase to 3% — your proposed 5% exceeds cap" | Notify Only |
| License/registration renewal | "Rental license for 220 Elm expires next month — renew?" | Notify Only |
| Fair housing flag | "Screening criteria may have disparate impact — review recommended" | Notify Only |

---

## ☁️ RentMate Hosted

If you want a managed, cloud-hosted version of RentMate, please let me know.

[**Join the Waitlist**](#)
