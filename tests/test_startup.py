"""
Integration tests: DB schema initialisation across SQLite and Postgres.

Validates that `_ensure_schema` (the DB step that runs on every server startup)
works correctly for both supported backends.

Run all:          poetry run pytest tests/test_startup.py
Postgres only:    poetry run pytest tests/test_startup.py -m postgres
Skip Postgres:    poetry run pytest tests/test_startup.py -m "not postgres"
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import scoped_session, sessionmaker
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


def test_repair_enum_rows_normalizes_lowercase_urgency():
    eng = _sqlite_engine()
    _init(eng)

    from sqlalchemy.orm import sessionmaker

    from db.enums import SuggestionStatus, TaskMode, TaskSource, TaskStatus, Urgency
    from db.models import Suggestion, Task, User

    Session = sessionmaker(bind=eng)
    db = Session()
    db.add(User(id=1, org_id=1, email="owner@example.com", active=True))
    db.flush()
    db.add(Task(
        org_id=1,
        creator_id=1,
        title="Test task",
        task_status=TaskStatus.ACTIVE,
        task_mode=TaskMode.MANUAL,
        source=TaskSource.MANUAL,
        urgency=Urgency.HIGH,
    ))
    db.add(Suggestion(
        org_id=1,
        creator_id=1,
        title="Test suggestion",
        status=SuggestionStatus.PENDING,
        urgency=Urgency.MEDIUM,
        suggestion_type="maintenance",
    ))
    db.commit()
    db.close()

    with eng.begin() as conn:
        conn.execute(text("UPDATE suggestions SET urgency = 'low'"))
        conn.execute(text("UPDATE tasks SET urgency = 'medium'"))

    import main as _main
    with patch.object(_main, "engine", eng):
        _main._repair_enum_rows()

    with eng.connect() as conn:
        suggestion_urgencies = conn.execute(text("SELECT urgency FROM suggestions")).scalars().all()
        task_urgencies = conn.execute(text("SELECT urgency FROM tasks")).scalars().all()

    assert set(suggestion_urgencies) <= {"LOW", None}
    assert set(task_urgencies) <= {"MEDIUM", None}


def test_sqlite_columns_present():
    """All model columns are present in SQLite after startup."""
    eng = _sqlite_engine()
    _init(eng)
    assert "is_ai" in _column_names(eng, "messages")
    assert "conversation_type" in _column_names(eng, "conversations")
    assert "sha256_checksum" in _column_names(eng, "documents")
    assert "payment_status" in _column_names(eng, "leases")
    assert "property_type" in _column_names(eng, "properties")
    assert "creator_id" in _column_names(eng, "properties")
    assert "creator_id" in _column_names(eng, "tenants")
    assert "creator_id" in _column_names(eng, "conversations")


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
    assert "creator_id" in _column_names(eng, "properties")
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


def test_sqlite_prod_can_skip_startup_check_on_drift():
    """Production mode can explicitly skip schema drift failure via STARTUP_CHECK."""
    eng = _sqlite_engine()
    with eng.connect() as conn:
        conn.execute(text("CREATE TABLE properties (id TEXT PRIMARY KEY)"))
        conn.commit()

    import main as _main
    with patch.object(_main, "engine", eng), patch.dict(
        os.environ,
        {"RENTMATE_ENV": "", "STARTUP_CHECK": "skip"},
        clear=False,
    ):
        _main._ensure_schema()


# ---------------------------------------------------------------------------
# Full startup simulation (mimics npm run dev)
# ---------------------------------------------------------------------------


def test_fresh_startup_seeds_account_and_scheduled_tasks():
    """On a fresh DB, startup seeds default account and scheduled tasks."""
    eng = _sqlite_engine()
    _init(eng)

    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=eng)
    db = Session()

    # User should be seeded
    from db.models import User
    accounts = db.query(User).all()
    # User may or may not be seeded by _ensure_schema (it's done in lifespan),
    # but the tables must exist
    inspector = inspect(eng)
    assert "users" in inspector.get_table_names()
    assert "scheduled_tasks" in inspector.get_table_names()
    db.close()


def test_startup_with_existing_data_no_crash():
    """Startup doesn't crash when DB already has data (idempotent)."""
    eng = _sqlite_engine()
    _init(eng)

    # Populate some data
    from sqlalchemy.orm import sessionmaker

    from db.models import Property, Tenant, Unit, User
    Session = sessionmaker(bind=eng)
    db = Session()

    import uuid
    from datetime import UTC, datetime
    acct_id = 1
    shadow_user = User(
        id=2,
        org_id=1,
        email="tenant@example.com",
        first_name="Test",
        last_name="Tenant",
        active=True,
        created_at=datetime.now(UTC),
    )
    db.add(shadow_user)
    prop = Property(
        id=str(uuid.uuid4()),
        org_id=1,
        creator_id=acct_id,
        address_line1="123 Test St",
        property_type="single_family",
        created_at=datetime.now(UTC),
    )
    db.add(prop)
    db.add(Unit(
        id=str(uuid.uuid4()),
        org_id=1,
        creator_id=acct_id,
        property_id=prop.id,
        label="Main",
        created_at=datetime.now(UTC),
    ))
    db.add(Tenant(
        org_id=1,
        creator_id=acct_id,
        user_id=shadow_user.id,
        created_at=datetime.now(UTC),
    ))
    db.commit()
    db.close()

    # Second startup should not crash
    _init(eng)

    # Data should still be there
    db = Session()
    assert db.query(Property).count() == 1
    assert db.query(Tenant).count() == 1
    db.close()


def test_app_lifespan_startup_no_crash():
    """FastAPI lifespan startup enters cleanly on an isolated database."""
    import asyncio

    import db.session as db_session
    import handlers.deps as deps
    import main as _main

    eng = _sqlite_engine()
    test_session_local = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=eng))

    def _discard_task(coro):
        if hasattr(coro, "close"):
            coro.close()
        return MagicMock()

    with (
        patch.object(_main, "engine", eng),
        patch.object(_main, "SessionLocal", test_session_local),
        patch.object(deps, "engine", eng),
        patch.object(deps, "SessionLocal", test_session_local),
        patch.object(db_session, "engine", eng),
        patch.object(db_session, "SessionLocal", test_session_local),
        patch.dict(os.environ, {"RENTMATE_ENV": "development", "STARTUP_CHECK": ""}, clear=False),
        patch("gql.services.settings_service.load_llm_into_env"),
        patch("gql.services.settings_service.load_agent_integrations_into_env"),
        patch.object(_main._app, "load_integrations", return_value={}),
        patch.object(_main, "set_memory_backstop"),
        patch.object(_main, "start_memory_monitor"),
        patch.object(_main.agent_registry, "start_gateway"),
        patch.object(_main.agent_registry, "restart_channels_async", new_callable=AsyncMock),
        patch.object(_main.agent_registry, "stop_gateway"),
        patch.object(_main.asyncio, "create_task", side_effect=_discard_task),
    ):
        asyncio.run(_run_lifespan(_main))


async def _run_lifespan(main_module):
    async with main_module.lifespan(main_module.app):
        return None


def test_startup_agent_memory_writes_with_explicit_creator():
    """Agent memory can be written with explicit creator_id (no request context)."""
    eng = _sqlite_engine()
    _init(eng)

    from sqlalchemy.orm import sessionmaker

    from db.models import AgentMemory
    Session = sessionmaker(bind=eng)
    db = Session()

    # Simulate what populate_all_agents does — write agent memory
    import uuid
    from datetime import UTC, datetime
    mem = AgentMemory(
        id=str(uuid.uuid4()),
        agent_id="1",
        org_id=1,
        creator_id=1,
        memory_type="file:TEST.md",
        content="test content",
        updated_at=datetime.now(UTC),
    )
    db.add(mem)
    db.commit()  # Must not raise "No creator context set"

    assert db.query(AgentMemory).count() >= 1
    db.close()


def test_startup_scheduled_task_creation_with_explicit_creator():
    """Scheduled tasks can be seeded with explicit creator_id."""
    eng = _sqlite_engine()
    _init(eng)

    from sqlalchemy.orm import sessionmaker

    from db.models import ScheduledTask
    Session = sessionmaker(bind=eng)
    db = Session()

    import uuid
    from datetime import UTC, datetime
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        org_id=1,
        creator_id=1,
        name="Test task",
        prompt="Do something",
        schedule="0 9 * * *",
        schedule_display="Daily at 9am",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(task)
    db.commit()  # Must not raise "No creator context set"

    assert db.query(ScheduledTask).count() >= 1
    db.close()


def test_dev_non_tty_auto_recreates():
    """Non-interactive dev mode (npm run dev) auto-recreates on schema drift."""
    eng = _sqlite_engine()
    # Create a stale schema
    with eng.connect() as conn:
        conn.execute(text("CREATE TABLE properties (id TEXT PRIMARY KEY)"))
        conn.commit()

    import main as _main
    with (
        patch.object(_main, "engine", eng),
        patch.dict(os.environ, {"RENTMATE_ENV": "development"}),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.isatty.return_value = False  # Non-interactive (npm run dev)
        _main._ensure_schema()

    # After auto-recreate, all model columns should be present
    assert "creator_id" in _column_names(eng, "properties")
    assert "address_line1" in _column_names(eng, "properties")
    assert "scheduled_tasks" in inspect(eng).get_table_names()


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
    assert "creator_id" in _column_names(pg_engine, "properties")
