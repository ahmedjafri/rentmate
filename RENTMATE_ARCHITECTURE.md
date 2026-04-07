# RentMate Architecture Reference (for eval writing)

## Entry Points
- `POST /chat/send` — User chat message (task or session)
- `POST /chat/assess` — Autonomous assessment trigger
- `POST /quo-webhook` — Inbound SMS from Quo/OpenPhone
- Gmail polling loop — Inbound emails routed to tasks
- `heartbeat_loop()` — 60s periodic scan for autonomous tasks needing attention
- `POST /automations/simulate` — Test automation rules

## Agent Flow
```
Message arrives → build_task_context() → call_agent(messages) → Agent runs tools → Reply + side_effects persisted
```

## Tools (llm/tools.py)
| Tool | Type | What it does |
|------|------|-------------|
| `propose_task` | Suggestion | Create new task (only for separate issues) |
| `close_task` | Immediate | Resolve task (enforces all steps done) |
| `set_mode` | Immediate | Change task mode (autonomous/manual/waiting_approval) |
| `attach_entity` | Suggestion | Link vendor/tenant/property/unit to task |
| `message_person` | Suggestion | Send message to tenant or vendor (+SMS) |
| `lookup_vendors` | Read-only | Search vendors by type/name |
| `update_steps` | Immediate | Set/update progress steps |
| `create_vendor` | Immediate | Create new vendor |
| `save_memory` | Immediate | Save task or entity notes |
| `recall_memory` | Read-only | Read saved notes |
| `edit_memory` | Immediate | Replace entity notes |

## Key Rules (from SOUL.md)
1. Never share tenant PII with vendors
2. Confirm with tenant FIRST before confirming vendor schedules
3. One task per issue — use attach_entity for second quotes
4. Missing data → escalate (set_mode waiting_approval), never guess
5. Never narrate tool calls
6. No internal operations in external messages
7. Task notes (scope=task) for job-specific data, entity context (scope=entity) for permanent knowledge

## Task Lifecycle
- Status: active → resolved (only when all steps done)
- Mode: autonomous | manual | waiting_approval
- Category: rent | maintenance | leasing | compliance | other
- Urgency: low | medium | high | critical

## Eval Pattern
1. Create DB fixtures (property, unit, tenant, lease, vendor, task)
2. Build messages with `_build_messages(db, task, user_msg)`
3. Run agent with `_run_agent_turn(db, task, msg)`
4. Assert on suggestions, DB state, and message quality (LLM judge)
5. Multi-turn: simulate replies with `_add_message()`, run agent again
