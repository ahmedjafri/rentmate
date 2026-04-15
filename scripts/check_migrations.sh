#!/bin/bash
# Pre-commit hook: fail if models have changed without a corresponding migration.
# Uses a temporary Postgres container so it exercises the real production dialect.
poetry run python scripts/check_migrations.py
