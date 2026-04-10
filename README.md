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
- **Scheduled Tasks** — Cron-based background tasks with natural language prompts (replaces YAML automation DSL)
- **SMS Integration** — Auto-reply to tenant texts via Quo/OpenPhone webhook
- **Multi-tenancy** — Per-account data isolation with request-scoped context
- **GraphQL API** — Full data access via Strawberry GraphQL
- **React UI** — Three-column dashboard with chat, tasks, and action desk

## Quickstart

Requires **Node >= 18** and **Python >= 3.12** with [Poetry](https://python-poetry.org/docs/#installation).

**One-line install** (macOS / Linux):
```bash
curl -fsSL https://raw.githubusercontent.com/ahmedjafri/rentmate/main/install.sh | bash
```

Or manually:
```bash
git clone https://github.com/ahmedjafri/rentmate.git && cd rentmate
npm install              # installs Python deps (poetry) + frontend

cp .env.example .env     # optional — LLM can be configured in the UI

npm run db:migrate       # apply database migrations
npm start                # starts API (port 8000)
```

For development:
```bash
npm run dev              # starts API (port 8002) + Vite dev server
                         # auto-recreates DB when schema changes
```

Open [http://localhost:8002](http://localhost:8002) (dev) or [http://localhost:8000](http://localhost:8000) (prod).

On first visit, click **Sign up** to create your account with an email and password. After signing in, the onboarding flow will guide you through connecting an AI model.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_API_KEY` | No | — | API key for your LLM provider (can also be set in Settings UI) |
| `LLM_MODEL` | No | `anthropic/claude-haiku-4-5-20251001` | LiteLLM model string |
| `LLM_BASE_URL` | No | — | Custom base URL (e.g. `http://localhost:11434` for Ollama) |
| `JWT_SECRET` | No | `rentmate-local-secret` | JWT signing secret — change in production |
| `RENTMATE_DATA_DIR` | No | `./data` | Data directory (DB, documents, agent workspace) |
| `QUO_API_KEY` | No | — | Quo/OpenPhone API key for SMS |
| `PHONE_WHITELIST` | No | — | Comma-separated phone numbers for auto-reply |

## Architecture

```
FastAPI backend (main.py)
  ├── /graphql            — Strawberry GraphQL API
  ├── /chat/send          — AI chat (SSE streaming)
  ├── /upload-document    — Document upload + extraction
  ├── /settings           — LLM and integration config
  ├── /onboarding/state   — First-run onboarding
  ├── /quo-webhook        — Inbound SMS from Quo
  └── /*                  — React SPA (www/rentmate-ui/dist/)

backends/
  ├── local_auth.py       — JWT auth with per-account bcrypt passwords
  ├── local_storage.py    — Filesystem document storage
  └── wire.py             — Backend wiring

db/
  ├── models/             — SQLAlchemy ORM (SQLite with WAL)
  ├── lib.py              — Legacy DB helpers
  └── queries.py          — Shared query functions

gql/
  ├── schema.py           — GraphQL queries and mutations
  └── services/           — Business logic (chat, tasks, settings, etc.)

llm/
  ├── client.py           — Agent execution + tool progress bridging
  ├── registry.py         — Agent registry, tool registration, system prompt
  ├── tools.py            — Agent tools (tasks, memory, documents, etc.)
  └── document_processor.py — PDF extraction + LLM parsing

handlers/
  ├── chat.py             — Chat endpoint, SSE streaming, heartbeat
  ├── settings.py         — LLM and integration settings
  ├── scheduler.py        — Scheduled task execution (cron)
  └── documents.py        — Document upload and processing

www/rentmate-ui/          — React 18 SPA (Vite + TypeScript + shadcn/ui)
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

FSL-1.1-Apache-2.0 — see [LICENSE](LICENSE). Companies under $10MM revenue may self-host freely.
