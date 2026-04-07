# AGENTS.md

Orientation for coding agents (Claude Code, Copilot, etc.) contributing to this repo.

**For full dev commands and architecture details, see [CLAUDE.md](./CLAUDE.md).**

---

## What is RentMate?

RentMate is an AI-driven property management assistant. It handles tenant communications, maintenance triage, lease lifecycle events, and automation — so landlords can own property without it owning their time.

The stack: FastAPI + Strawberry GraphQL backend, React/Vite frontend, SQLAlchemy ORM on SQLite, LiteLLM AI agent.

---

## Design Documentation (`docs/`)

These docs define product behavior and the automation DSL. **Keep them in sync with the code.** If you add a feature, automation type, or change existing behavior, update the relevant doc. If a doc is stale or wrong, fix it as part of the same PR.

### `docs/product.md` — Product vision & autonomy model

Defines what RentMate does and how the AI suggestion system works. Key concepts:

- **47 suggestion types** across 8 categories (rent, maintenance, tenant communication, leasing, lease lifecycle, move-in/out, financial, compliance)
- **4 autonomy levels:** Notify Only → Suggest & Wait → Auto with Review Window → Fully Autonomous
- **Autonomy ceilings:** Each suggestion type has a maximum autonomy level that cannot be exceeded regardless of owner preference. Examples:
  - Security deposit deductions: max `Suggest & Wait`
  - Legal notices / non-renewal: max `Notify Only`
  - Tenant rejection/approval: max `Suggest & Wait`
  - Late rent reminders: max `Fully Autonomous`

### `docs/property-flow.md` — Property-Flow DSL v1.1 spec

Defines the YAML automation language used by the Automations feature.

- **Pipeline:** Translate (LLM) → Validate (JSON Schema) → Execute (Runner)
- **Scope resources:** `property`, `unit`, `lease`, `tenant`
- **Operators:** `equals`, `not_equals`, `gt`, `lt`, `gte`, `lte`, `in`, `exists`, `not_exists`, `contains`
- **Actions:** `create_task` (implemented), `send_notification` / `update_record` (planned)
- **Variable syntax:** `{{lease.tenant.first_name}}`, `{{days_until_end}}`, `{{params.warn_days}}`
- **6 built-in checks** defined in DSL: `vacant_units`, `expiring_leases`, `overdue_rent`, `incomplete_properties`, `missing_contact`, `expired_leases`

---

## Key Architecture Highlights

> CLAUDE.md has the full picture — this is the quick orientation.

| Layer | Location | Notes |
|---|---|---|
| FastAPI entry | `main.py` | Mounts `/graphql`, Quo webhook, `/suggest-reply`, static SPA |
| Business logic | `db/lib.py` | All DB operations; multi-tenant scoping lives here |
| ORM models | `db/models.py` | `Account → Property → Unit → Lease → Tenant`; messaging layer |
| GraphQL | `gql/` | Strawberry schema; queries scoped to authenticated user's account |
| AI agent | `llm/` | LiteLLM AI agent; system prompt in `agents/template/SOUL.md` |
| Automation DSL | `automations/` + `db/dsl_runner.py` | Built-in checks in `automations/*.json` |
| HTTP handlers | `handlers/` | `handlers/chat.py` owns Quo webhook + agent invocation |
| Frontend | `www/rentmate-ui/` | React/Vite; built to `dist/` served statically by FastAPI |
| Migrations | `db/migrations/versions/` | Alembic migrations |

---

## Agent Identity Files (`agents/template/`)

These files define the AI agent's runtime persona and behavior. They are copied to `data/agent/` at runtime.

- **`SOUL.md`** — Persona, responsibilities, critical boundaries (what the agent must never do), escalation protocol. The "never do" rules here are hard constraints enforced at the prompt level.
- **`AGENTS.md`** — Session startup sequence, memory system, group chat/reaction etiquette.
- **`IDENTITY.md`** — Name, role, vibe.
- **`HEARTBEAT.md`** — Periodic background check template.

When modifying agent behavior, edit these template files. The system prompt that actually runs is assembled from them plus `llm/.context/index.md`.

---

## Critical Constraints for Coding Agents

### Multi-tenancy — never break this
Every database query must be scoped to `account_id`. There are no exceptions. Check `db/lib.py` for the pattern — every function that touches data takes an `account_id` and filters on it.

### Autonomy ceilings — never remove or exceed them
The autonomy ceiling per suggestion type in `docs/product.md` must be enforced in code. Don't add code paths that allow, e.g., legal notices or deposit deductions to fire without human approval.

### Property-Flow DSL — must stay schema-validated
The automation DSL must pass JSON Schema validation before execution (`db/dsl_runner.py`). There must be no path to executing arbitrary code through an automation script — all field references, operators, and action types are drawn from a fixed allowlist.

### Frontend changes
Before finishing any frontend change:
1. Run `npx tsc --noEmit` and fix all type errors
2. Verify every identifier (especially icon imports from `lucide-react`) is in the import list

### Tests use real Postgres
Tests spin up a real Postgres instance via `testcontainers`. Do not mock the DB. Do not call `db.commit()` inside test fixtures — the per-test savepoint rollback pattern depends on this.

---

## Development Quickref

```bash
# Backend
./run.sh dev                          # dev server (port 8002, auto-reload)
poetry run pytest                     # all tests
poetry run alembic upgrade head       # apply migrations

# Frontend
cd www/rentmate-ui
npm run dev                           # backend + frontend together
npm run build                         # production build → dist/
npx tsc --noEmit                      # type-check only
```

See [CLAUDE.md](./CLAUDE.md) for the complete command reference and environment variable requirements.
