# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

Tests use `testcontainers` to spin up a real Postgres instance automatically — no manual DB setup required.

### Database migrations
```bash
poetry run alembic revision --autogenerate -m "description"  # generate migration
poetry run alembic upgrade head                               # apply migrations
```

Migrations live in `db/migrations/versions/`. The `alembic.ini` `sqlalchemy.url` is a placeholder; the real URL is configured via the DB engine in `db/session.py`.

### Frontend
```bash
cd www/rentmate-ui
npm install
npm run build      # builds into www/rentmate-ui/dist/ (served by FastAPI)
npm run dev        # backend + frontend together, with prefixed logs from both
npm run dev:fe     # Vite dev server only (port 8080, proxies to backend on 8002)
```

## Environment Variables

Required at runtime:
- `DEEPSEEK_API_KEY` — Used by LiteLLM for the AI agent

## Architecture

### Backend 3-layer design

The backend follows a strict **handlers → services → models** layering:

1. **Handlers** (`handlers/`, `gql/schema.py` mutations) — HTTP/GraphQL entry points. Responsible for auth, request validation, orchestrating multiple service calls, creating/finding conversations, and managing transactions (savepoints). Handlers decide *what* to do and in what order.

2. **Services** (`gql/services/`) — Stateless business logic. Each service operates on the DB session it receives. Services must NOT create conversations or manage cross-entity orchestration — that belongs in the handler. Import services as modules (`from gql.services import chat_service`) not individual functions.

3. **Models** (`db/models/`) — SQLAlchemy ORM definitions. Pure data layer, no business logic.

Key rules:
- Conversation creation/lookup always happens at the handler layer via `chat_service.get_or_create_external_conversation()`. Services like `TaskService.create_task()` create only the task and its AI conversation; the handler is responsible for the external conversation.
- `db/lib.py` contains lower-level DB helpers (SMS recording, tenant upserts) that predate the service layer. New business logic should go in `gql/services/`.

### Backend (Python / FastAPI)

**`main.py`** — FastAPI entry point. Mounts:
- `/graphql` — Strawberry GraphQL API
- `/quo-webhook` — Receives inbound SMS from Quo (OpenPhone), records them, and triggers the AI agent for whitelisted numbers
- `/suggest-reply` — Called by the Chrome extension to get an AI-suggested reply
- `/*` — Catch-all serves the React SPA from `www/rentmate/dist/`

Authentication uses JWTs validated per-request in `get_context()`. The resolved user is passed into GraphQL resolvers via context.

**`db/models/`** — SQLAlchemy ORM models (SQLite):
- `Account` → `Property` → `Unit` → `Lease` → `Tenant` (core rental hierarchy)
- `AccountUser` — links users to an `Account` with a role (`admin`, `manager`, `tenant`)
- `Conversation` / `ConversationParticipant` / `Message` / `MessageReceipt` — messaging layer, participants can be tenants, account users, or external contacts (vendors)

**`gql/services/`** — Business logic services:
- `task_service.py` — Task CRUD, vendor assignment metadata, message persistence
- `chat_service.py` — Conversation lookup/creation (`get_or_create_external_conversation`), autonomous messaging, typing indicators, message history
- `property_service.py`, `tenant_service.py`, `vendor_service.py`, `document_service.py` — Domain services

**`handlers/`** — HTTP route handlers (automations, chat, vendor portal, etc.). Orchestrate service calls, manage transactions, and handle external conversation wiring.

**`gql/schema.py`** — GraphQL query/mutation definitions. Mutations act as handlers: they resolve auth, call services, and wire conversations.

**`db/lib.py`** — Legacy DB helpers (SMS recording, tenant upserts). Predates the service layer.

**`llm/`** — AI agent:
- `client.py` — `chat_with_agent()` initializes the AI agent, runs conversations, bridges progress events. `call_agent()` dispatches to hosted or local agent.
- `tools.py` — Agent tools (propose_task, close_task, message_person, attach_entity, etc.)
- `registry.py` — Agent registry, tool registration, system prompt building from `agents/template/SOUL.md`

### Frontend (`www/rentmate/`)

React SPA (Vite). Built output is served statically by FastAPI. The frontend communicates exclusively via the `/graphql` endpoint.

### Chrome Extension (`chrome/`)

Browser extension for TenantCloud (`app.tenantcloud.com`). `content.js` injects UI into the page; `suggestion.js` calls `/suggest-reply` to get AI-drafted responses.

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

- When using a service from `gql/services/`, import the module itself rather than individual functions. Use `from gql.services import chat_service` and call `chat_service.should_ai_respond(...)`, not `from gql.services.chat_service import should_ai_respond`.
- All data is multi-tenant: every query is scoped to an `account_id` resolved from the authenticated user's `AccountUser` record.
- The Quo integration is the primary inbound channel for SMS. Phone number normalization (`db/utils.py`) is critical for matching tenants/admins.
- Tests use per-test transaction rollback (savepoints) for isolation — do not call `db.commit()` inside test fixtures.
- The agent system prompt is in `llm/.context/index.md` — edit that file to change RentMate's persona/responsibilities.
