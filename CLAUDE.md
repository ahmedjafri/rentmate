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

### Backend (Python / FastAPI)

**`main.py`** — FastAPI entry point. Mounts:
- `/graphql` — Strawberry GraphQL API
- `/dialpad-webhook` — Receives inbound/outbound SMS from Dialpad, records them, and triggers the AI agent for whitelisted numbers
- `/suggest-reply` — Called by the Chrome extension to get an AI-suggested reply
- `/*` — Catch-all serves the React SPA from `www/rentmate/dist/`

Authentication uses Supabase JWTs validated per-request in `get_context()`. The resolved user is passed into GraphQL resolvers via context.

**`db/models.py`** — SQLAlchemy ORM models (PostgreSQL via Supabase):
- `Account` → `Property` → `Unit` → `Lease` → `Tenant` (core rental hierarchy)
- `AccountUser` — links Supabase `auth.users` to an `Account` with a role (`admin`, `manager`, `tenant`)
- `Conversation` / `ConversationParticipant` / `Message` / `MessageReceipt` — messaging layer, participants can be tenants, account users, or external contacts (vendors)

**`db/lib.py`** — Business logic layer over the ORM. Key functions:
- `record_sms_from_dialpad()` — resolves account/tenant/direction from phone numbers and persists the SMS as a `Message`
- `get_or_create_tenant_by_phone()` / `get_or_create_conversation_for_tenant()` — upsert helpers
- `_find_account_for_dialpad_numbers()` — determines inbound vs. outbound by matching phone numbers against `AccountUser.phone` (admin) and `Tenant.phone`

**`gql/`** — Strawberry GraphQL schema:
- `schema.py` — assembles `Query` + merged `Mutation` (auth + app mutations)
- `queries.py` — `me`, `houses`, `tenants`, `leases` queries scoped to the authenticated user's account
- `auth_mutations.py` / `other_mutations.py` — separated by concern

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

- All data is multi-tenant: every query is scoped to an `account_id` resolved from the authenticated user's `AccountUser` record.
- The Dialpad integration is the primary inbound channel for SMS. Phone number normalization (`db/utils.py`) is critical for matching tenants/admins.
- Tests use per-test transaction rollback (savepoints) for isolation — do not call `db.commit()` inside test fixtures.
- The agent system prompt is in `llm/.context/index.md` — edit that file to change RentMate's persona/responsibilities.
