# RentMate

[![Tests](https://github.com/ahmedjafri/rentmate/actions/workflows/test.yml/badge.svg)](https://github.com/ahmedjafri/rentmate/actions/workflows/test.yml)
[![License: FSL-1.1-Apache-2.0](https://img.shields.io/badge/License-FSL--1.1--Apache--2.0-blue.svg)](LICENSE)

> **Pre-alpha software** — This project is under active development and is not ready for production use. APIs, database schemas, and features may change without notice.

A fair-source, agent-first property management platform.

## Why RentMate

Most PropTech software was built a decade ago and is now bolting AI onto existing workflows — a chatbot here, a copilot there. The core is still rigid forms, manual steps, and rule-based automation. AI is an afterthought.

RentMate flips this. The AI agent is the foundation, not a feature. Tenant messages, maintenance coordination, vendor outreach, lease monitoring — the agent handles these end-to-end, and the software exists to give it structure: data models, approval workflows, and a UI for when you want to stay in the loop.

The difference matters in practice. When AI is a layer on top, every new capability requires new screens, new buttons, new integrations. When AI is the core, new capabilities are just new context and tools the agent can use. You describe what you want, and it figures out the how.

**For small landlords:** RentMate replaces the patchwork of spreadsheets, texts, and property management apps with a single agent that actually manages.

**For property managers:** RentMate handles the repetitive coordination — tenant follow-ups, vendor scheduling, lease tracking — so you focus on decisions, not data entry.

## Features

- **AI Chat** — Conversational interface backed by any OpenAI-compatible model (OpenAI, Anthropic, DeepSeek, Ollama, etc.)
- **Document Processing** — Upload lease PDFs and automatically extract tenant, property, unit, and lease data
- **Onboarding** — Guided first-run setup walks you through AI configuration and property setup
- **Suggestions** — Agent-generated action items with risk scoring and approval workflows
- **Routines** — Cron-based background runs with natural language prompts (replaces YAML automation DSL)
- **SMS Integration** — Auto-reply to tenant texts via Quo/OpenPhone webhook
- **Multi-tenancy** — Per-account data isolation with request-scoped context
- **GraphQL API** — Full data access via Strawberry GraphQL
- **React UI** — Three-column dashboard with chat, tasks, and action desk

## Quickstart

Requires **Node >= 18** and **Python >= 3.12** with [Poetry](https://python-poetry.org/docs/#installation).
RentMate now uses **PostgreSQL** for both application data and vector storage. For local development, `npm run dev` starts the full dev stack, including PostgreSQL, in Docker.

Agent-generated PDF documents now render through a backend-owned **WeasyPrint** runtime. If you want the `create_document` tool to work locally, install the Python dependencies and the required system libraries for WeasyPrint.

**One-line install** (macOS / Linux):
```bash
curl -fsSL https://raw.githubusercontent.com/ahmedjafri/rentmate/main/install.sh | bash
```

Or manually:
```bash
git clone https://github.com/ahmedjafri/rentmate.git && cd rentmate
poetry install
npm install              # installs frontend deps for the React UI

cp .env.example .env     # optional — LLM can be configured in the UI
```

For development:
```bash
npm run dev              # starts the containerized dev stack
                         # PostgreSQL under ./data/db, API on :8002, Vite on :8080
```

Stop it with:
```bash
npm run dev:down
```

Open [http://localhost:8080](http://localhost:8080) (dev) or [http://localhost:8000](http://localhost:8000) (prod).

For a non-Docker startup against an already-running Postgres instance:
```bash
export RENTMATE_DB_URI=postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/rentmate
npm run db:migrate
npm start
```

On first visit, click **Sign up** to create your account with an email and password. After signing in, the onboarding flow will guide you through connecting an AI model.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_API_KEY` | No | — | API key for your LLM provider (can also be set in Settings UI) |
| `LLM_MODEL` | No | `anthropic/claude-haiku-4-5-20251001` | LiteLLM model string |
| `LLM_BASE_URL` | No | — | Custom base URL (e.g. `http://localhost:11434` for Ollama) |
| `JWT_SECRET` | No | `rentmate-local-secret` | JWT signing secret — change in production |
| `RENTMATE_DB_URI` | No | `postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/rentmate` | PostgreSQL connection string |
| `RENTMATE_DATA_DIR` | No | `./data` | Data directory (documents, agent workspace, local dev bind mounts) |
| `RENTMATE_DEPLOYMENT_MODE` | No | `single-machine` | Deployment topology: `single-machine` or `distributed` |
| `RENTMATE_DOC_GEN_BACKEND` | No | derived from deployment mode | Override doc-gen transport: `local` or `grpc` |
| `RENTMATE_DOC_GEN_GRPC_TARGET` | No | `127.0.0.1:50061` | gRPC target the main API uses when doc-gen is distributed |
| `RENTMATE_DOC_GEN_GRPC_BIND` | No | `0.0.0.0:50061` | Bind address for the standalone doc-gen gRPC service |
| `RENTMATE_DOC_GEN_GRPC_INSECURE` | No | `true` | Use insecure gRPC transport for internal doc-gen traffic |
| `RENTMATE_DOC_GEN_RENDER_TIMEOUT_MS` | No | `30000` | Render timeout for local and gRPC doc-gen requests |
| `QUO_API_KEY` | No | — | Quo/OpenPhone API key for SMS |
| `PHONE_WHITELIST` | No | — | Comma-separated phone numbers for auto-reply |

## Document Rendering

- The `create_document` agent tool renders HTML/CSS to PDF using WeasyPrint.
- Local development requires the backend Poetry environment plus the native libraries WeasyPrint depends on.
- In `single-machine` mode, the API renders documents locally in the main backend process.
- In `distributed` mode, the API calls a dedicated doc-gen service over gRPC. Start that worker with `poetry run python scripts/run_doc_gen_service.py`.
- If the WeasyPrint native libraries are missing, agent-created documents will fail at render time even if the rest of the app is working.
- Generated documents store both the final PDF and the rendered HTML source in the document storage area.

## Containers

- Backend container builds install Poetry-managed Python dependencies and the native libraries WeasyPrint needs during image build.
- If you build your own OCI image outside the included Dockerfiles, make sure the image includes the Cairo/Pango/GDK PixBuf runtime dependencies WeasyPrint requires.
- `npm run dev` now uses [infra/docker-compose.dev.yml](infra/docker-compose.dev.yml) to run a bind-mounted dev stack:
  - `postgres` runs pgvector-enabled PostgreSQL with data stored in `./data/db`
  - `api` runs `python main.py --reload --port 8002`
  - `web` runs Vite on port `8080`
  - source changes on the host are reflected live inside both containers

## Architecture

```
FastAPI backend (main.py)
  ├── /graphql            — Strawberry GraphQL API
  ├── /chat/send          — AI chat (SSE streaming)
  ├── /upload-document    — Document upload + extraction
  ├── /api/settings       — LLM and integration config
  ├── /onboarding/state   — First-run onboarding
  ├── /quo-webhook        — Inbound SMS from Quo
  └── /*                  — React SPA (www/rentmate-ui/dist/)

backends/
  ├── local_auth.py       — JWT auth with per-account bcrypt passwords
  ├── local_storage.py    — Filesystem document storage
  └── wire.py             — Backend wiring

db/
  ├── models/             — SQLAlchemy ORM (PostgreSQL)
  ├── lib.py              — Legacy DB helpers
  └── queries.py          — Shared query functions

gql/
  ├── schema.py           — GraphQL queries and mutations
  └── services/           — Business logic (chat, tasks, settings, etc.)

llm/
  ├── client.py           — Agent execution + tool progress bridging
  ├── doc_gen_runtime.py  — Local vs gRPC document-generation abstraction
  ├── registry.py         — Agent registry, tool registration, system prompt
  ├── tools.py            — Agent tools (tasks, memory, documents, etc.)
  └── document_processor.py — PDF extraction + LLM parsing

handlers/
  ├── chat.py             — Chat endpoint, SSE streaming, routine
  ├── settings.py         — LLM and integration settings
  ├── routines.py  — Routine execution (cron)
  └── documents.py        — Document upload and processing

www/rentmate-ui/          — React 18 SPA (Vite + TypeScript + shadcn/ui)
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

FSL-1.1-Apache-2.0 — see [LICENSE](LICENSE). Companies under $10MM revenue may self-host freely.
