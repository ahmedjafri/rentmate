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

Migrations live in `db/migrations/versions/`. The `alembic.ini` `sqlalchemy.url` is a placeholder; the real URL comes from the `SUPABASE_DB_URI` env var.

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
- `SUPABASE_URL` — Supabase project URL
- `SUPABASE_SECRET_KEY` — Supabase service role secret key
- `SUPABASE_DB_URI` — Direct Postgres connection string (`postgresql+psycopg2://...`)
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
- `/dialpad-webhook` — Receives inbound/outbound SMS from Dialpad, records them, and triggers the AI agent for whitelisted numbers
- `/suggest-reply` — Called by the Chrome extension to get an AI-suggested reply
- `/*` — Catch-all serves the React SPA from `www/rentmate/dist/`

Authentication uses Supabase JWTs validated per-request in `get_context()`. The resolved user is passed into GraphQL resolvers via context.

**`db/models/`** — SQLAlchemy ORM models (PostgreSQL via Supabase):
- `Account` → `Property` → `Unit` → `Lease` → `Tenant` (core rental hierarchy)
- `AccountUser` — links Supabase `auth.users` to an `Account` with a role (`admin`, `manager`, `tenant`)
- `Conversation` / `ConversationParticipant` / `Message` / `MessageReceipt` — messaging layer, participants can be tenants, account users, or external contacts (vendors)

**`gql/services/`** — Business logic services:
- `task_service.py` — Task CRUD, vendor assignment metadata, message persistence
- `chat_service.py` — Conversation lookup/creation (`get_or_create_external_conversation`), autonomous messaging, typing indicators, message history
- `property_service.py`, `tenant_service.py`, `vendor_service.py`, `document_service.py` — Domain services

**`handlers/`** — HTTP route handlers (automations, chat, vendor portal, etc.). Orchestrate service calls, manage transactions, and handle external conversation wiring.

**`gql/schema.py`** — GraphQL query/mutation definitions. Mutations act as handlers: they resolve auth, call services, and wire conversations.

**`db/lib.py`** — Legacy DB helpers (SMS recording, tenant upserts). Predates the service layer.

**`llm/`** — AI agent:
- `llm.py` — Initializes `ChatLiteLLM` with DeepSeek (`deepseek/deepseek-chat`), loads system prompt from `llm/.context/index.md`
- `agent.py` — `RentMateAgent` built with LangGraph: LLM node → conditional tool call → tool node loop. `run_agent()` accepts a context string + conversation history list and returns the agent's text response.

### Frontend (`www/rentmate/`)

React SPA (Vite). Built output is served statically by FastAPI. The frontend communicates exclusively via the `/graphql` endpoint.

### Chrome Extension (`chrome/`)

Browser extension for TenantCloud (`app.tenantcloud.com`). `content.js` injects UI into the page; `suggestion.js` calls `/suggest-reply` to get AI-drafted responses.

## Code Quality Rules

Before finishing any frontend change:
1. Run `npx tsc --noEmit` and fix all type errors
2. Verify every used identifier (especially icon imports from lucide-react) is present in the import list — Vite/tsc won't catch missing runtime bindings that exist globally elsewhere

### Key design patterns

- When using a service from `gql/services/`, import the module itself rather than individual functions. Use `from gql.services import chat_service` and call `chat_service.should_ai_respond(...)`, not `from gql.services.chat_service import should_ai_respond`.
- All data is multi-tenant: every query is scoped to an `account_id` resolved from the authenticated user's `AccountUser` record.
- The Dialpad integration is the primary inbound channel for SMS. Phone number normalization (`db/utils.py`) is critical for matching tenants/admins.
- Tests use per-test transaction rollback (savepoints) for isolation — do not call `db.commit()` inside test fixtures.
- The agent system prompt is in `llm/.context/index.md` — edit that file to change RentMate's persona/responsibilities.
