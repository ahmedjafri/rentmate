# CLAUDE.md

Guidance for coding agents (Claude Code, Copilot, etc.) working in this repo.

## What is RentMate?

AI-driven property management assistant. Handles tenant communications, maintenance triage, lease lifecycle events, and automations — so landlords can own property without it owning their time.

Stack: FastAPI + Strawberry GraphQL backend, React/Vite frontend, SQLAlchemy ORM on SQLite, LiteLLM-based AI agent.

## Commands

### Running the server
```bash
./run.sh start    # production (port 8000)
./run.sh dev      # development with reload (port 8002)
```

### Running tests
```bash
poetry run pytest                          # all tests
poetry run pytest tests/test_db_lib.py    # single file
poetry run pytest -k "test_name"          # single test by name
```

Tests use `testcontainers` to spin up a real Postgres instance — no manual DB setup required.

### Database migrations
```bash
poetry run alembic revision --autogenerate -m "description"  # generate migration
poetry run alembic upgrade head                               # apply migrations
poetry run alembic check                                      # verify models match migrations
npm run db:reset                                              # delete dev DB (recreated on next startup)
```

Dev mode (`npm run dev` / `run.sh dev`) auto-recreates the DB when schema changes — no manual migration needed.
Production (`npm start`) requires explicit `alembic upgrade head` before startup.

A pre-commit hook and CI both verify that model changes have corresponding migration files.
Migrations live in `db/migrations/versions/`.

### Frontend
```bash
cd www/rentmate-ui
npm install
npm run build      # builds into www/rentmate-ui/dist/ (served by FastAPI)
npm run dev        # backend + frontend together, with prefixed logs from both
npm run dev:fe     # Vite dev server only (port 8080, proxies to backend on 8002)
```

## Environment Variables

Key variables (set in `data/settings.json` or shell):
- `LLM_MODEL` — Model identifier (default: `anthropic/claude-haiku-4-5-20251001`)
- `LLM_API_KEY` — API key for LLM provider
- `RENTMATE_AGENT_URL` — If set, uses hosted agent instead of local
- `RENTMATE_DATA_DIR` — Data directory (default: `./data`)
- `RENTMATE_ENV` — `development` enables debug features and seeds automations
- `QUO_API_KEY` — OpenPhone API key for SMS
- `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `GMAIL_SENDER_ADDRESS` — Gmail OAuth (optional)
- `PHONE_WHITELIST` — Comma-separated phone numbers allowed to trigger agent

## Architecture

### Backend 3-layer design

Strict **handlers → services → models** layering:

1. **Handlers** (`handlers/`, `gql/schema.py` mutations) — HTTP/GraphQL entry points. Responsible for auth, request validation, orchestrating service calls, creating/finding conversations, and managing transactions. Handlers decide *what* to do and in what order.

2. **Services** (`gql/services/`) — Stateless business logic. Each service operates on the DB session it receives. Services must NOT create conversations or manage cross-entity orchestration — that belongs in the handler. Import services as modules (`from gql.services import chat_service`) not individual functions.

3. **Models** (`db/models/`) — SQLAlchemy ORM definitions (SQLite with WAL mode). Pure data layer, no business logic.

Key rules:
- Conversation creation/lookup always happens at the handler layer via `chat_service.get_or_create_external_conversation()`. Services like `TaskService.create_task()` create only the task and its AI conversation; the handler is responsible for the external conversation.
- `db/lib.py` contains lower-level DB helpers (SMS recording, tenant upserts, inbound message routing) that predate the service layer. New business logic should go in `gql/services/`.

### Backend detail

**`main.py`** — FastAPI entry point. Mounts routers, middleware, and the SPA catch-all. On startup: creates DB tables, runs schema migrations, starts the agent gateway, and kicks off polling loops (Gmail, Quo SMS, automation audit/heartbeat). Also runnable directly (`python main.py --port 8002 --reload`).

**`db/models/`** — SQLAlchemy ORM models:
- Rental hierarchy: `Account` → `Property` → `Unit` → `Lease` → `Tenant`
- `AccountUser` — links users to an `Account` with a role (`admin`, `manager`, `tenant`)
- Messaging: `Conversation`, `ConversationParticipant`, `Message`, `MessageReceipt` — participants can be tenants, account users, or external contacts (vendors)
- Tasks: `Task`, `TaskNumberSequence` — per-account monotonic task numbering
- Documents: `Document`, `DocumentTask`, `DocumentTag`
- AI: `AgentMemory`, `AgentTrace`
- Automations: `AutomationRevision`
- Settings: `AppSetting`

**`db/session.py`** — SQLAlchemy engine and `SessionLocal` factory. SQLite DB at `data/rentmate.db`.

**`db/lib.py`** — Legacy DB helpers: `route_inbound_to_task()`, `route_inbound_to_tenant_chat()`, `get_or_create_tenant_by_phone()`, `spawn_task_from_conversation()`.

**`db/dsl_runner.py`** — Property-Flow DSL interpreter for YAML automation scripts.

**`gql/services/`** — Business logic services:
- `chat_service.py` — Conversation lookup/creation, autonomous messaging, typing indicators
- `task_service.py` — Task CRUD, vendor assignment, message persistence
- `sms_service.py` — SMS sending via Quo
- `document_service.py` — Document extraction and embedding
- `suggestion_service.py` / `task_suggestions.py` — Task suggestion creation and execution
- `settings_service.py` — App and integration settings
- `portal_auth.py` — Tenant/vendor portal authentication
- `property_service.py`, `tenant_service.py`, `vendor_service.py` — Domain services

**`gql/schema.py`** — Strawberry GraphQL query/mutation definitions. Mutations act as handlers: resolve auth, call services, wire conversations.

**`handlers/`** — HTTP route handlers:
- `chat.py` — Agent chat execution, streaming responses, progress events
- `automations.py` — Automation DSL execution, audit and heartbeat loops
- `documents.py` — Document upload, extraction, embedding
- `settings.py` — Integration configuration (Quo, Gmail, etc.)
- `quo_poller.py` — SMS polling from Quo/OpenPhone (5 min dev, 15 min prod)
- `tenant_portal.py`, `vendor_portal.py` — Portal endpoints
- `tenant_invite.py`, `vendor_invite.py` — Onboarding link handlers

**`backends/`** — Pluggable backend abstractions:
- `wire.py` — Wiring for all backends
- `local_auth.py` — Dev single-tenant auth
- `local_storage.py` — Local filesystem storage
- `chroma_vector.py` — Chroma vector DB for document embeddings
- `single_tenant_sms.py` — Dev SMS routing
- `gmail.py` — Gmail OAuth client (polling + send)

**`llm/`** — AI agent:
- `client.py` — `chat_with_agent()` / `call_agent()` dispatch to local or hosted agent
- `tools.py` — Agent tools: propose_task, close_task, message_person, attach_entity, lookup/create vendors, save/recall/edit memory, update_steps
- `registry.py` — Agent registry, tool registration, gateway lifecycle, system prompt assembly
- `context.py` — Builds system prompt and task context
- `side_effects.py` — Processes agent tool results (recording messages, creating tasks)
- `memory_store.py` — DB-backed memory (entity context columns + `agent_memory` table)
- `tracing.py` — Logs agent operations for debugging
- `document_processor.py` — PDF text extraction and LLM-based parsing

**`agents/template/`** — Agent identity files (copied to `data/agent/` at runtime):
- `SOUL.md` — Persona, responsibilities, hard constraints, escalation protocol
- `AGENTS.md` — Session startup, memory system, group chat etiquette
- `IDENTITY.md` — Name, role, vibe
- `HEARTBEAT.md` — Periodic background check template

**`automations/`** — Built-in automation definitions (JSON). DSL-based YAML scripts interpreted by `db/dsl_runner.py`. Stored as `AutomationRevision` rows.

### Frontend (`www/rentmate-ui/`)

React 18 SPA (Vite + TypeScript). shadcn/ui components, TanStack React Query, Tailwind CSS. Built output served statically by FastAPI. Communicates exclusively via `/graphql`.

### Inbound channels

- **SMS (Quo/OpenPhone)** — Primary channel. Webhook at `/quo-webhook` + backup poller in `handlers/quo_poller.py`. Phone normalization in `db/utils.py`.
- **Email (Gmail)** — Optional. Polling loop in `main.py` every 60s. Routes to tasks via `route_inbound_to_task()`.

## Design Documentation (`docs/`)

These docs define product behavior and the automation DSL. **Keep them in sync with the code.**

- **`docs/product.md`** — Product vision, 47 suggestion types across 8 categories, 4 autonomy levels (Notify Only → Fully Autonomous), autonomy ceilings per suggestion type.
- **`docs/property-flow.md`** — Property-Flow DSL v1.1 spec. YAML automation language: translate → validate → execute pipeline.

## Code Quality Rules

### Before finishing ANY change:
1. Run `poetry run ruff check .` and fix all errors (use `--fix` for auto-fixable issues like import sorting)
2. Run `poetry run python scripts/lint_kwargs.py` and fix any new violations you introduced
3. Run `poetry run pytest tests/` and ensure all tests pass

### Before finishing any frontend change:
1. Run `npx tsc --noEmit` and fix all type errors
2. Verify every used identifier (especially icon imports from lucide-react) is present in the import list — Vite/tsc won't catch missing runtime bindings that exist globally elsewhere

### Python lint rules (enforced by ruff + scripts/lint_kwargs.py)

**Import hygiene:**
- All imports MUST be at the top of the file — no lazy imports inside functions. If you need to avoid a circular import, refactor the dependency instead.
- No unused imports (F401) — remove imports you don't use.
- Imports must be sorted and consolidated (isort rules).

**Keyword-only parameters:**
- Public functions (not `_` prefixed) in `db/`, `gql/`, `backends/`, `llm/`, `handlers/` with 3+ parameters (after self/cls) must use keyword-only arguments. Put `*,` after the first parameter:
  ```python
  # Good
  def fetch_tasks(db, *, category=None, status=None, source=None): ...

  # Bad
  def fetch_tasks(db, category=None, status=None, source=None): ...
  ```

**Private imports:**
- Do not import private symbols (prefixed with `_`) from `db/`, `gql/`, `backends/`, `llm/` modules. If you need the functionality, make it public or refactor.

### Key design patterns

- Import service modules, not individual functions: `from gql.services import chat_service` then `chat_service.should_ai_respond(...)`.
- All data is multi-tenant: every query scoped to `account_id` from the authenticated user's `AccountUser` record.
- Tests use per-test transaction rollback (savepoints) for isolation — do not call `db.commit()` inside test fixtures.
- The agent system prompt is assembled from `agents/template/` files + `llm/.context/index.md`.

### Critical constraints

- **Multi-tenancy** — Every DB query must be scoped to `account_id`. No exceptions.
- **Autonomy ceilings** — Each suggestion type in `docs/product.md` has a max autonomy level that cannot be exceeded. Don't add code paths that bypass human approval for restricted actions (legal notices, deposit deductions, etc.).
- **Property-Flow DSL** — Must pass JSON Schema validation before execution. No path to executing arbitrary code through automation scripts.
