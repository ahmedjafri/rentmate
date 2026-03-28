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
    assert "task_id" in _column_names(eng, "conversations")
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
    assert "task_id" in _column_names(eng, "conversations")
    assert "conversation_type" in _column_names(eng, "conversations")
    assert "is_ai" in _column_names(eng, "messages")
    assert "sha256_checksum" in _column_names(eng, "documents")
    assert "payment_status" in _column_names(eng, "leases")


def test_migrate_schema_covers_all_orm_columns():
    """
    Guard against the gap where a column is added to the ORM model but not to
    _migrate_schema(), causing failures on existing databases that were created
    before create_all would have added the column.

    Strategy: build each covered table with only its 'original' base columns
    (the columns that predated any _migrate_schema additions), run
    _migrate_schema() alone, then assert every ORM column is present.

    When you add a new column to the ORM for one of these tables you MUST also
    add it to _MIGRATE_COLS in main.py — or this test will tell you.
    """
    import main as _main

    # Original schema for each table — columns that have always existed and are
    # therefore guaranteed to be on any real database without needing ALTER TABLE.
    # Columns added after the initial deploy belong in _MIGRATE_COLS instead.
    BASE_DDL = [
        """CREATE TABLE properties (
            id TEXT PRIMARY KEY,
            account_id TEXT,
            address_line1 TEXT NOT NULL,
            address_line2 TEXT,
            name TEXT,
            city TEXT,
            state TEXT,
            postal_code TEXT,
            country TEXT,
            created_at DATETIME NOT NULL
        )""",
        """CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            account_id TEXT,
            property_id TEXT,
            unit_id TEXT,
            lease_id TEXT,
            subject TEXT,
            is_group BOOLEAN NOT NULL DEFAULT 0,
            is_archived BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )""",
        """CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            body TEXT,
            body_html TEXT,
            sent_at DATETIME,
            is_system BOOLEAN,
            sender_type TEXT,
            sender_tenant_id TEXT,
            sender_external_contact_id TEXT,
            meta TEXT,
            attachments TEXT,
            edited_at DATETIME,
            deleted_at DATETIME
        )""",
        """CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            account_id TEXT,
            filename TEXT,
            storage_path TEXT,
            document_type TEXT,
            status TEXT,
            content_type TEXT,
            created_at DATETIME,
            processed_at DATETIME,
            raw_text TEXT,
            extracted_data TEXT,
            error_message TEXT,
            progress TEXT
        )""",
        """CREATE TABLE leases (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            unit_id TEXT NOT NULL,
            property_id TEXT NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            rent_amount REAL NOT NULL,
            created_at DATETIME NOT NULL
        )""",
    ]

    eng = _sqlite_engine()
    with eng.connect() as conn:
        for ddl in BASE_DDL:
            conn.execute(text(ddl))
        conn.commit()

    with patch.object(_main, "engine", eng):
        _main._migrate_schema()

    migrated_tables = {t for t, _, _ in _main._MIGRATE_COLS}
    orm_cols_by_table = {
        t.name: {col.name for col in t.columns}
        for t in Base.metadata.tables.values()
        if t.name in migrated_tables
    }
    missing = []
    for table, orm_cols in orm_cols_by_table.items():
        db_cols = _column_names(eng, table)
        for col in orm_cols:
            if col not in db_cols:
                missing.append(f"{table}.{col}")

    assert not missing, (
        "These ORM columns are absent after _migrate_schema() — add them to "
        "_MIGRATE_COLS in main.py:\n  " + "\n  ".join(sorted(missing))
    )


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
    assert "task_id" in _column_names(pg_engine, "conversations")
    assert "sha256_checksum" in _column_names(pg_engine, "documents")
    assert "payment_status" in _column_names(pg_engine, "leases")
    assert "property_type" in _column_names(pg_engine, "properties")
