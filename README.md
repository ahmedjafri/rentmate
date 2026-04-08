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

- **AI Chat** — Ask questions about your tenants, leases, and properties
- **Document Processing** — Upload lease PDFs and automatically extract tenant/property/lease data
- **SMS Integration** — Auto-reply to tenant texts via Quo webhook
- **GraphQL API** — Full data access via a Strawberry GraphQL endpoint
- **React UI** — Built-in web interface for property management

## Quickstart

Requires **Node ≥18** and **Python ≥3.12** with [Poetry](https://python-poetry.org/docs/#installation).

**One-line install** (macOS / Linux):
```bash
curl -fsSL https://raw.githubusercontent.com/ahmedjafri/rentmate/main/install.sh | bash
```

Or manually:
```bash
git clone https://github.com/ahmedjafri/rentmate.git && cd rentmate
npm install              # installs Python deps (poetry) + frontend

cp .env.example .env
# Edit .env — set LLM_API_KEY to any OpenAI-compatible key

npm run db:migrate       # apply database migrations
npm start                # starts API (port 8000)
```

For development:
```bash
npm run dev              # starts API (port 8002) + Vite dev server
                         # auto-recreates DB when schema changes
```

Open [http://localhost:8002](http://localhost:8002) (dev) or [http://localhost:8000](http://localhost:8000) (prod) and log in with the password from `RENTMATE_PASSWORD` (default: `rentmate`).

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_API_KEY` | Yes | — | API key for your LLM provider |
| `LLM_MODEL` | No | `openai/gpt-4o-mini` | LiteLLM model string |
| `LLM_BASE_URL` | No | — | Custom base URL (e.g. Ollama) |
| `RENTMATE_PASSWORD` | No | `rentmate` | Login password |
| `JWT_SECRET` | No | `rentmate-local-secret` | JWT signing secret — change in production |
| `RENTMATE_DB_PATH` | No | `./data/rentmate.db` | SQLite database path |
| `RENTMATE_DOCS_DIR` | No | `./data/documents` | Document storage directory |
| `RENTMATE_CHROMA_DIR` | No | `./data/chroma` | ChromaDB vector store path |
| `QUO_API_KEY` | No | — | Quo API key for SMS |
| `PHONE_WHITELIST` | No | — | Comma-separated phone numbers for auto-reply |

## Architecture

```
FastAPI backend (main.py)
  ├── /graphql          — Strawberry GraphQL API
  ├── /auth/login       — JWT authentication
  ├── /upload-document  — Document upload + processing
  ├── /chat             — AI chat endpoint
  ├── /quo-webhook  — Inbound SMS from Quo
  └── /*                — React SPA (www/rentmate/dist/)

backends/
  ├── base.py           — Abstract interfaces
  ├── local_auth.py     — JWT auth (single user)
  ├── local_storage.py  — Filesystem document storage
  ├── chroma_vector.py  — ChromaDB vector store
  ├── single_tenant_sms.py — SMS routing
  └── wire.py           — Backend wiring

db/
  ├── models.py         — SQLAlchemy ORM (SQLite)
  └── lib.py            — Business logic

llm/
  ├── client.py         — AI agent + tool progress
  └── document_processor.py — PDF extraction + embedding
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

FSL-1.1-Apache-2.0 — see [LICENSE](LICENSE). Companies under $10MM revenue may self-host freely.
