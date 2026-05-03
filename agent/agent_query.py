#!/usr/bin/env python3
"""
agent_query.py — Live-data SQL executor for OpenClaw agents.

Usage:
    python /path/to/llm/agent_query.py --agent-id <uuid> "SELECT ..."

Security model:
- Only SELECT / WITH...SELECT statements are allowed
- Blocked patterns: information_schema, semicolons
- Read-only connection (no commit path)
- Results capped at 200 rows
"""

import argparse
import json
import os
import re
import sys

from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Blocked patterns (checked before execution)
# ---------------------------------------------------------------------------

_BLOCKED_PATTERNS = [
    re.compile(r"\binformation_schema\b", re.IGNORECASE),
    re.compile(r"\bpg_catalog\b", re.IGNORECASE),
    re.compile(r";"),  # no multiple statements
]

_ALLOWED_START = re.compile(r"^\s*(with\b|select\b)", re.IGNORECASE)

MAX_ROWS = 200


def _validate_sql(sql: str) -> str | None:
    """Return an error string if the SQL is not allowed, else None."""
    if not _ALLOWED_START.match(sql):
        return "Only SELECT or WITH...SELECT queries are allowed."
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(sql):
            return f"Query contains a blocked pattern: {pattern.pattern!r}"
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="RentMate agent data query tool")
    parser.add_argument("--agent-id", required=True, help="Agent identity (ignored in OSS, single tenant)")
    parser.add_argument("sql", help="SELECT query to execute")
    args = parser.parse_args()

    db_uri = os.environ.get("RENTMATE_DB_URI", "").strip() or "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/rentmate"

    # Validate SQL before touching the DB
    err = _validate_sql(args.sql)
    if err:
        print(json.dumps({"error": err}))
        sys.exit(0)

    try:
        engine = create_engine(db_uri)
        with engine.connect() as conn:
            result = conn.execute(text(args.sql))
            columns = list(result.keys())
            rows = result.fetchmany(MAX_ROWS)
            data = [dict(zip(columns, [str(v) if v is not None else None for v in r])) for r in rows]

            output = {"columns": columns, "rows": data, "count": len(data)}
            if len(data) == MAX_ROWS:
                output["truncated"] = True
            print(json.dumps(output, default=str))

    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(0)


if __name__ == "__main__":
    main()
