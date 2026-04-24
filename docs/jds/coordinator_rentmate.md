# RentMate for the Property Management Coordinator role

RentMate is an AI property manager that handles the day-to-day coordination,
communication, and documentation work a Property Management Coordinator does.
This doc walks through each responsibility in
[`coordinator.md`](./coordinator.md) and shows — with concrete examples — how
RentMate can take over, augment, or short-circuit it.

The common pattern: RentMate drafts, coordinates, and logs. For anything that
reaches a tenant, vendor, or owner, the property manager sees a suggestion on
the **Action Desk** and approves with one click — unless the action is low-risk
enough to auto-send under the configured autonomy rules.

## 1. Leasing — listings, applicants, lease prep

**What a coordinator does:** keep listings fresh, respond to applicants, track
who applied, prepare lease agreements, make sure paperwork is filed.

**What RentMate does:**

- **Intake a lease from a PDF.** Drop a signed lease into the chat. RentMate
  extracts the property address, unit, tenant name, rent, term, deposit, and
  lease-context notes (pet policy, utilities, late fees, etc.) and proposes
  creating the records. If anything is missing — say, a blank tenant name —
  RentMate stops and asks you to fill it in rather than silently inventing a
  placeholder.
- **Applicant follow-up.** When a prospect texts in, RentMate responds, pulls the unit's availability + rent + terms, answers the
  question, and logs the thread against the unit record. It won't make
  screening decisions — those come to you — but it keeps the applicant warm.
- **Generate notices and lease addenda as PDFs.** "Draft a 30-day rent
  increase notice for Unit 4B at $50/mo starting Aug 1" produces a formatted
  PDF in seconds, filed against the lease.

**Example day-in-the-life:** A prospect texts at 9pm asking whether pets are
allowed. RentMate replies with the pet policy from the lease template, asks
a qualifying question, and — if the policy is "cats only, deposit $300" —
sends it. When the applicant decides to apply, RentMate logs the conversation
and creates a task for you: "Screen applicant Jamie Kim for Unit 4B."

**Still yours:** publishing to Zillow/Apartments.com, making the screening
call, signing the final lease.

## 2. Tenant communication

**What a coordinator does:** answer tenant questions all day — rent, repairs,
amenities, general support.

**What RentMate does:**

- **Replies to tenant SMS automatically.** An inbound text to your
  property-line number becomes an agent conversation. RentMate pulls the
  tenant's lease + unit + open tasks, drafts a reply, and either sends it
  (routine: scheduling, FAQ, confirmations) or parks it as a suggestion
  (sensitive: rent, notices, anything legal-flavored).
- **Threaded context per issue.** Every tenant message gets linked to the
  right task. A follow-up two days later picks up where the last one left
  off — including any vendor thread that's been coordinating behind the
  scenes.
- **Manager chat is the same agent.** "What's open on 123 Main this week?"
  gives you a live answer instead of a spreadsheet query.

**Example scenarios:**

- Tenant: "Can I paint the living room?" → RentMate pulls the lease's
  modification clause, drafts the right answer, and sends it without
  bothering you.
- Tenant: "I'm going to be late on rent this month." → RentMate does NOT
  auto-reply. It parks a suggestion with a draft in your tone, flagged for
  you to adjust before it goes out.
- Tenant: "The smoke detector is chirping." → RentMate opens a
  maintenance task, drafts a vendor outreach, and pings you if it's an
  emergency (CO / gas / water).

**Still yours:** habitability escalations, eviction conversations, rent
renegotiations — drafted by RentMate, approved by you.

## 3. Maintenance coordination

**What a coordinator does:** take the request, call vendors, get quotes,
schedule the work, update the tenant, close the loop.

**What RentMate does:**

- **Turns every request into a task.** Tenant SMS, chat message, or
  voicemail transcript → one task with category, urgency, and a short
  summary. Duplicate reports get attached to the same task instead of
  piling up.
- **Gets the quote.** RentMate picks an appropriate vendor from your
  roster (or asks you to pick if it's unsure), drafts the outreach, and
  sends it. The vendor's reply comes back into the same task thread.
- **Schedules access.** RentMate coordinates the vendor's window with the
  tenant — "Vendor says Thursday 10–12, does that work for you?" — and
  only finalizes once both sides are confirmed.
- **Keeps the record.** Every message, quote, scheduling change, and
  status update is timestamped on the task. Two months later you can
  open it and see exactly what happened.
- **Remembers vendor preferences.** "Acme Plumbing wants 48 hours' notice
  and invoices at month-end" gets saved against the vendor and shows up
  automatically on the next job.

**Example scenarios:**

- Dripping faucet report → RentMate categorizes it as routine, picks your
  go-to plumber, sends an "is this on your list?" message, and if the
  plumber says $80 flat-rate they'll swing by Wednesday, auto-schedules
  the access conversation with the tenant.
- Water heater failure at 2am → RentMate pages you with the urgency flag,
  drafts a "we're on it" reply to the tenant, and lines up the after-hours
  vendor call.

**Still yours:** signing off on quotes above your autonomy threshold
(default: anything over $500 or anything requiring the owner's approval).

## 4. Delinquencies and payment follow-up

**What a coordinator does:** check who's late, send reminders, escalate,
track partial payments.

**What RentMate does:**

- **Scheduled rent review.** Set up a recurring agent task: "On the 1st
  and 6th of every month, review all leases with payment_status != current
  and draft a reminder for each one." Reminders land on the Action Desk
  for a batch approval.
- **Graceful reminder ladder.** First reminder is friendly, second is
  firmer, third escalates to notice territory — but the agent never
  advances to a legal notice autonomously. That's your call.
- **Partial payment tracking.** Tenant says "paid $800 of $1200 this
  month, rest by the 15th" → RentMate updates the lease payment status,
  schedules a follow-up on the 15th, and drafts a confirmation back to
  the tenant.
- **Formal notices.** When you decide to serve a 14-day pay-or-vacate,
  RentMate drafts the jurisdiction-specific notice (with the statutorily
  required fields), but always hands it to you for physical service.

**Example scenario:** Three tenants are late on the 5th. RentMate queues
three reminder suggestions on the Action Desk — same tone, each
personalized with the tenant's name and balance. You click "approve all,"
they go out, and RentMate schedules a second-pass review for the 10th.

**Still yours:** anything that touches courts, eviction filings, or
security-deposit disputes.

## 5. Move-in and move-out documentation

**What a coordinator does:** walk-through checklist, photos, keys, deposit
paperwork, final ledger.

**What RentMate does:**

- **Move-in kit.** Generates the welcome packet, unit condition form,
  lease summary PDF, and parking/key receipt when a new tenant is
  created.
- **Move-out checklist.** When notice-to-vacate is received, RentMate
  opens a move-out task with every sub-step: schedule final inspection,
  capture condition notes, generate the deposit-reconciliation PDF,
  forward address on file.
- **Inspection upload.** Drop the inspection photos and your scribbled
  notes into the task. RentMate summarizes them into the condition
  report and pre-fills the deposit itemization.
- **Deposit math.** Given the lease deposit, any outstanding balance,
  and your itemized deductions, RentMate produces the deposit-refund
  letter formatted to state requirements.

**Example scenario:** A tenant gives 30-day notice. RentMate opens the
task, schedules the inspection, drafts the tenant confirmation, and —
24 hours before move-out — reminds you to do the walkthrough. Post-
walkthrough, you upload photos, RentMate builds the itemized deposit
letter, you review the dollar figures, and it goes out.

**Still yours:** the walkthrough itself, the judgment call on what's
"normal wear and tear."

## 6. Insurance, compliance, and property records

**What a coordinator does:** keep a folder (or spreadsheet) of everything
regulatory — insurance certs, smoke-detector inspections, rental licenses,
lead-paint disclosures, code-enforcement letters.

**What RentMate does:**

- **Everything uploaded is indexed.** Drop the insurance renewal PDF
  into the chat; RentMate tags it to the property, records the expiration
  date, and reminds you 30 days before it lapses.
- **Queries work against the pile.** "When does the master policy on 123
  Main expire?" "Which units had smoke detectors serviced in 2026?"
  "Show me every lead-paint disclosure for pre-1978 properties." All
  work as chat prompts.
- **Compliance calendars.** Recurring safety checks (annual fire
  inspection, quarterly smoke-detector battery test) turn into scheduled
  tasks that ping you automatically.
- **Structured context on every entity.** Each property, unit, tenant,
  and vendor has a running context note that RentMate keeps up to date
  as new info arrives — so the story of a unit across tenants lives in
  one place instead of five spreadsheets.

**Example scenario:** A city code-enforcement letter arrives about a
handrail at 456 Oak. You photograph it into the chat. RentMate recognizes
the property, logs the compliance item, creates a task for the repair,
and schedules a follow-up check for the city's 30-day deadline.

**Still yours:** the decision to renew, file, contest, or escalate.

## 7. Basic accounting data entry

**What a coordinator does:** enter invoices, record payments, keep the
books current.

**What RentMate does:**

- **Invoice intake.** A vendor emails (or texts) an invoice PDF;
  RentMate reads the amount, vendor, property, service date, and creates
  an expense suggestion on the Action Desk for your approval. One click
  and it's recorded.
- **Payment status.** When you confirm "Alice paid June rent" in chat,
  or an inbound integration flags a payment, RentMate updates the lease
  record and any open delinquency thread.
- **Receipts and summaries.** "Show me everything I spent on 123 Main
  this quarter" or "what did I pay Acme Plumbing last year?" works as
  a chat query.

**Current boundary:** RentMate doesn't push entries to QuickBooks or
AppFolio accounting yet — suggestion cards are the source of truth, and
reconciliation is still manual. That integration is a known gap.

## 8. General admin support

**What a coordinator does:** whatever the manager hands them — reports,
letters, emails, one-off errands.

**What RentMate does:**

- **Ad-hoc prompts.** "Summarize everything that happened on 123 Main
  last week." "Draft an email to all tenants in Building B about the
  water shutoff Thursday 9–11am." "Which units are coming up for renewal
  in the next 90 days?"
- **Batch operations.** Act on a list in one go — "send a holiday
  greeting to every current tenant," "queue gutter cleaning on all
  Washington properties before November 1." Each item gets its own task
  or suggestion; you approve as a batch.
- **Turn anything recurring into a schedule.** "Every Monday morning,
  give me a summary of overdue tasks" becomes a scheduled agent prompt
  with zero code.

## Where RentMate today *cannot* replace the coordinator

- **External listing sites** (Zillow, Apartments.com): no direct write
  integration yet — you still post the ad.
- **Accounting write-back** (QuickBooks, AppFolio accounting): inbound
  invoice extraction yes, outbound ledger posting no.
- **Physical presence**: showings, walkthroughs, in-person key handoff.
- **High-stakes judgment calls**: eviction filings, insurance renewals,
  legal responses — drafted by RentMate, decided by you.

## The net effect

A coordinator's full-time day-to-day collapses into a few minutes of
Action Desk review. The drafting, coordination, and logging happen
continuously in the background. Your attention goes to decisions —
the 10% of the role that's actually hard — while the 90% of
paperwork-and-follow-up runs itself.
