"""
Integration tests: DB schema initialisation across SQLite and Postgres.

Validates that `Base.metadata.create_all` + `_migrate_schema` (the two DB steps
that run on every server startup) work correctly and are idempotent for both
supported backends.

Run all:          poetry run pytest tests/test_startup.py
Postgres only:    poetry run pytest tests/test_startup.py -m postgres
Skip Postgres:    poetry run pytest tests/test_startup.py -m "not postgres"
"""

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from db.models import Base


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init(eng):
    """Run the full startup DB initialisation sequence against the given engine."""
    import main as _main
    with patch.object(_main, "engine", eng):
        Base.metadata.create_all(eng)
        _main._migrate_schema()


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
    """_migrate_schema runs without error on a fresh SQLite database."""
    _init(_sqlite_engine())


def test_sqlite_idempotent():
    """Calling _migrate_schema twice on SQLite must not raise."""
    eng = _sqlite_engine()
    import main as _main
    with patch.object(_main, "engine", eng):
        Base.metadata.create_all(eng)
        _main._migrate_schema()
        _main._migrate_schema()


def test_sqlite_columns_present():
    """All migrated columns are present in SQLite after startup."""
    eng = _sqlite_engine()
    _init(eng)
    assert "is_ai" in _column_names(eng, "messages")
    assert "is_task" in _column_names(eng, "conversations")
    assert "sha256_checksum" in _column_names(eng, "documents")
    assert "payment_status" in _column_names(eng, "leases")
    assert "property_type" in _column_names(eng, "properties")


def test_sqlite_migrate_on_partial_schema():
    """_migrate_schema adds missing columns to tables that predate the migration."""
    eng = _sqlite_engine()
    # Simulate an old database: create the tables via raw SQL, omitting the
    # columns that _migrate_schema is responsible for adding.
    old_schema_ddl = [
        "CREATE TABLE properties (id TEXT PRIMARY KEY, account_id TEXT, name TEXT)",
        "CREATE TABLE conversations (id TEXT PRIMARY KEY, account_id TEXT, subject TEXT)",
        "CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT, body TEXT)",
        "CREATE TABLE documents (id TEXT PRIMARY KEY, account_id TEXT, filename TEXT)",
        "CREATE TABLE leases (id TEXT PRIMARY KEY, unit_id TEXT, start_date TEXT)",
    ]
    with eng.connect() as conn:
        for ddl in old_schema_ddl:
            conn.execute(text(ddl))
        conn.commit()

    import main as _main
    with patch.object(_main, "engine", eng):
        _main._migrate_schema()

    assert "property_type" in _column_names(eng, "properties")
    assert "is_task" in _column_names(eng, "conversations")
    assert "is_ai" in _column_names(eng, "messages")
    assert "sha256_checksum" in _column_names(eng, "documents")
    assert "payment_status" in _column_names(eng, "leases")


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
    """_migrate_schema runs without error on a fresh Postgres database."""
    _init(pg_engine)


@pytest.mark.postgres
def test_postgres_idempotent(pg_engine):
    """Calling _migrate_schema twice on Postgres must not raise."""
    import main as _main
    with patch.object(_main, "engine", pg_engine):
        # create_all is idempotent itself; just test _migrate_schema twice
        _main._migrate_schema()
        _main._migrate_schema()


@pytest.mark.postgres
def test_postgres_columns_present(pg_engine):
    """All migrated columns are present in Postgres after startup."""
    assert "is_ai" in _column_names(pg_engine, "messages")
    assert "is_task" in _column_names(pg_engine, "conversations")
    assert "sha256_checksum" in _column_names(pg_engine, "documents")
    assert "payment_status" in _column_names(pg_engine, "leases")
    assert "property_type" in _column_names(pg_engine, "properties")
