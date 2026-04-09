"""
Integration tests: DB schema initialisation across SQLite and Postgres.

Validates that `_ensure_schema` (the DB step that runs on every server startup)
works correctly for both supported backends.

Run all:          poetry run pytest tests/test_startup.py
Postgres only:    poetry run pytest tests/test_startup.py -m postgres
Skip Postgres:    poetry run pytest tests/test_startup.py -m "not postgres"
"""

import os
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init(eng):
    """Run the full startup DB initialisation sequence against the given engine."""
    import main as _main
    with patch.object(_main, "engine", eng), patch.dict(os.environ, {"RENTMATE_ENV": "development"}):
        _main._ensure_schema()


def _column_names(eng, table: str) -> set[str]:
    inspector = inspect(eng)
    return {col["name"] for col in inspector.get_columns(table)}


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

def _sqlite_engine():
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_sqlite_clean_startup():
    """_ensure_schema runs without error on a fresh SQLite database."""
    _init(_sqlite_engine())


def test_sqlite_idempotent():
    """Calling _ensure_schema twice on SQLite must not raise."""
    eng = _sqlite_engine()
    import main as _main
    with patch.object(_main, "engine", eng), patch.dict(os.environ, {"RENTMATE_ENV": "development"}):
        _main._ensure_schema()
        _main._ensure_schema()


def test_sqlite_columns_present():
    """All model columns are present in SQLite after startup."""
    eng = _sqlite_engine()
    _init(eng)
    assert "is_ai" in _column_names(eng, "messages")
    assert "conversation_type" in _column_names(eng, "conversations")
    assert "sha256_checksum" in _column_names(eng, "documents")
    assert "payment_status" in _column_names(eng, "leases")
    assert "property_type" in _column_names(eng, "properties")
    assert "account_id" in _column_names(eng, "properties")
    assert "account_id" in _column_names(eng, "tenants")
    assert "account_id" in _column_names(eng, "conversations")


def test_sqlite_dev_recreates_on_drift():
    """Dev mode detects schema drift and recreates the database."""
    eng = _sqlite_engine()
    # Create a minimal table missing many columns
    with eng.connect() as conn:
        conn.execute(text("CREATE TABLE properties (id TEXT PRIMARY KEY)"))
        conn.commit()

    import main as _main
    with (
        patch.object(_main, "engine", eng),
        patch.dict(os.environ, {"RENTMATE_ENV": "development"}),
        patch("sys.stdin") as mock_stdin,
        patch("builtins.input", return_value="w"),
    ):
        mock_stdin.isatty.return_value = True
        _main._ensure_schema()

    # After recreate, all model columns should be present
    assert "account_id" in _column_names(eng, "properties")
    assert "address_line1" in _column_names(eng, "properties")


def test_sqlite_prod_fails_on_drift():
    """Production mode raises SystemExit when schema is stale."""
    eng = _sqlite_engine()
    with eng.connect() as conn:
        conn.execute(text("CREATE TABLE properties (id TEXT PRIMARY KEY)"))
        conn.commit()

    import main as _main
    with patch.object(_main, "engine", eng), patch.dict(os.environ, {"RENTMATE_ENV": ""}, clear=False):
        with pytest.raises(SystemExit):
            _main._ensure_schema()


# ---------------------------------------------------------------------------
# Postgres (requires Docker)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pg_engine():
    """Spin up a real Postgres container shared across all postgres tests."""
    from testcontainers.postgres import PostgresContainer
    with PostgresContainer("postgres:16-alpine") as pg:
        eng = create_engine(pg.get_connection_url())
        yield eng
        eng.dispose()


@pytest.mark.postgres
def test_postgres_clean_startup(pg_engine):
    """_ensure_schema runs without error on a fresh Postgres database."""
    _init(pg_engine)


@pytest.mark.postgres
def test_postgres_idempotent(pg_engine):
    """Calling _ensure_schema twice on Postgres must not raise."""
    import main as _main
    with patch.object(_main, "engine", pg_engine), patch.dict(os.environ, {"RENTMATE_ENV": "development"}):
        _main._ensure_schema()
        _main._ensure_schema()


@pytest.mark.postgres
def test_postgres_columns_present(pg_engine):
    """All model columns are present in Postgres after startup."""
    assert "is_ai" in _column_names(pg_engine, "messages")
    assert "conversation_type" in _column_names(pg_engine, "conversations")
    assert "sha256_checksum" in _column_names(pg_engine, "documents")
    assert "payment_status" in _column_names(pg_engine, "leases")
    assert "property_type" in _column_names(pg_engine, "properties")
    assert "account_id" in _column_names(pg_engine, "properties")
