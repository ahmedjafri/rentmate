# Contributing to RentMate

## Development Setup

```bash
npm install          # installs Python deps (poetry) + frontend
cp .env.example .env
# Set LLM_API_KEY in .env
```

## Common Commands

```bash
npm run dev          # API (port 8002) + Vite dev server, both with hot reload
npm run build        # production frontend build (served by FastAPI)
npm start            # production server (port 8000)
npm test             # run all tests
```

## Running Tests

```bash
npm test                                         # all tests
poetry run pytest tests/test_db_lib.py          # single file
poetry run pytest -k "test_name"                # single test
```

Tests use an in-memory SQLite database — no external services required.

## Database Migrations

```bash
poetry run alembic revision --autogenerate -m "description"
poetry run alembic upgrade head
```

## Pull Request Process

1. Fork the repository and create a branch
2. Make your changes with tests
3. Run `npm test` and ensure all tests pass
4. Open a pull request with a clear description

## Code Style

- Python: follow PEP 8, use type hints where helpful
- Keep functions focused — prefer small, testable units
- Don't add dependencies without discussion

## Questions?

Open an issue on GitHub.
