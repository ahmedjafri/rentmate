#!/bin/bash
# Pre-commit hook: fail if models have changed without a corresponding migration.
# Uses a temporary in-memory SQLite DB so it doesn't touch the real one.
export DATABASE_URL="sqlite://"
output=$(poetry run alembic check 2>&1)
status=$?
if [ $status -ne 0 ]; then
    echo "$output"
    echo ""
    echo "Model changes detected without a migration."
    echo "Run: poetry run alembic revision --autogenerate -m 'description'"
    exit 1
fi
