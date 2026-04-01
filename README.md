# RentMate

> **Pre-alpha software** — This project is under active development and is not ready for production use. APIs, database schemas, and features may change without notice.

An open source AI-powered property management assistant. Chat with your properties, tenants, and leases using natural language.

## Features

- **AI Chat** — Ask questions about your tenants, leases, and properties
- **Document Processing** — Upload lease PDFs and automatically extract tenant/property/lease data
- **SMS Integration** — Auto-reply to tenant texts via Dialpad webhook
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

npm run dev              # starts API (port 8002) + Vite dev server
```

Open [http://localhost:8002](http://localhost:8002) and log in with the password from `RENTMATE_PASSWORD` (default: `rentmate`).

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
| `DIALPAD_API_KEY` | No | — | Dialpad API key for SMS |
| `PHONE_WHITELIST` | No | — | Comma-separated phone numbers for auto-reply |

## Architecture

```
FastAPI backend (main.py)
  ├── /graphql          — Strawberry GraphQL API
  ├── /auth/login       — JWT authentication
  ├── /upload-document  — Document upload + processing
  ├── /chat             — AI chat endpoint
  ├── /dialpad-webhook  — Inbound SMS from Dialpad
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
  ├── agent.py          — LangGraph AI agent
  └── document_processor.py — PDF extraction + embedding
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
