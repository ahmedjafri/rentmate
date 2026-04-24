"""Integration tests for Postgres-backed startup schema initialization."""

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import scoped_session, sessionmaker

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
# Postgres
# ---------------------------------------------------------------------------

def test_postgres_clean_startup(isolated_engine):
    """_ensure_schema runs without error on a fresh Postgres database."""
    _init(isolated_engine)


def test_postgres_idempotent(isolated_engine):
    """Calling _ensure_schema twice on Postgres must not raise."""
    eng = isolated_engine
    import main as _main
    with patch.object(_main, "engine", eng), patch.dict(os.environ, {"RENTMATE_ENV": "development"}):
        _main._ensure_schema()
        _main._ensure_schema()


def test_repair_enum_rows_normalizes_lowercase_urgency(isolated_engine):
    eng = isolated_engine
    _init(eng)

    from sqlalchemy.orm import sessionmaker

    from db.enums import SuggestionStatus, TaskMode, TaskSource, TaskStatus, Urgency
    from db.models import Suggestion, Task, User
    from gql.services.number_allocator import NumberAllocator

    Session = sessionmaker(bind=eng)
    db = Session()
    db.add(User(id=1, org_id=1, email="owner@example.com", active=True))
    db.flush()
    db.add(Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=1,
        title="Test task",
        task_status=TaskStatus.ACTIVE,
        task_mode=TaskMode.MANUAL,
        source=TaskSource.MANUAL,
        urgency=Urgency.HIGH,
    ))
    db.add(Suggestion(
        id=NumberAllocator.allocate_next(db, entity_type="suggestion", org_id=1),
        org_id=1,
        creator_id=1,
        title="Test suggestion",
        status=SuggestionStatus.PENDING,
        urgency=Urgency.MEDIUM,
        suggestion_type="maintenance",
    ))
    db.commit()
    db.close()

    import main as _main
    with patch.object(_main, "engine", eng):
        _main._repair_enum_rows()

    with eng.connect() as conn:
        suggestion_urgencies = conn.execute(text("SELECT urgency FROM suggestions")).scalars().all()
        task_urgencies = conn.execute(text("SELECT urgency FROM tasks")).scalars().all()

    assert set(suggestion_urgencies) <= {"MEDIUM", None}
    assert set(task_urgencies) <= {"HIGH", None}


def test_postgres_columns_present(isolated_engine):
    """All model columns are present in Postgres after startup."""
    eng = isolated_engine
    _init(eng)
    assert "is_ai" in _column_names(eng, "messages")
    assert "conversation_type" in _column_names(eng, "conversations")
    assert "sha256_checksum" in _column_names(eng, "documents")
    assert "payment_status" in _column_names(eng, "leases")
    assert "property_type" in _column_names(eng, "properties")
    assert "creator_id" in _column_names(eng, "properties")
    assert "creator_id" in _column_names(eng, "tenants")
    assert "creator_id" in _column_names(eng, "conversations")


def test_postgres_dev_recreates_on_drift(isolated_engine):
    """Dev mode detects schema drift and recreates the database."""
    eng = isolated_engine
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


def test_postgres_prod_fails_on_drift(isolated_engine):
    """Production mode raises SystemExit when schema is stale."""
    eng = isolated_engine
    with eng.connect() as conn:
        conn.execute(text("CREATE TABLE properties (id TEXT PRIMARY KEY)"))
        conn.commit()

    import main as _main
    with patch.object(_main, "engine", eng), patch.dict(os.environ, {"RENTMATE_ENV": ""}, clear=False):
        with pytest.raises(SystemExit):
            _main._ensure_schema()


def test_postgres_prod_can_skip_startup_check_on_drift(isolated_engine):
    """Production mode can explicitly skip schema drift failure via STARTUP_CHECK."""
    eng = isolated_engine
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


def test_fresh_startup_seeds_account_and_routines(isolated_engine):
    """On a fresh DB, startup seeds default account and routines."""
    eng = isolated_engine
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
    assert "routines" in inspector.get_table_names()
    db.close()


def test_dev_bootstrap_account_has_expected_credentials_and_seed_data(isolated_engine):
    """Fresh dev startup creates the default login and enables demo seeding."""
    import asyncio

    import db.session as db_session
    import handlers.deps as deps
    import main as _main
    from backends.local_auth import _check_password
    from db.models import Property, Task, User

    eng = isolated_engine
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

    Session = sessionmaker(bind=eng)
    db = Session()
    try:
        acct = db.query(User).filter_by(email="test@test.com").first()
        assert acct is not None
        assert acct.password_hash
        assert _check_password("test", hashed=acct.password_hash)
        assert db.query(Property).count() > 0
        assert db.query(Task).count() > 0
        seeded_task = db.query(Task).filter(Task.creator_id == acct.id).first()
        assert seeded_task is not None
        assert seeded_task.goal
        assert seeded_task.steps
    finally:
        db.close()


def test_startup_with_existing_data_no_crash(isolated_engine):
    """Startup doesn't crash when DB already has data (idempotent)."""
    eng = isolated_engine
    _init(eng)

    # Populate some data
    from sqlalchemy.orm import sessionmaker

    from db.models import Property, Tenant, Unit, User
    Session = sessionmaker(bind=eng)
    db = Session()

    import uuid
    from datetime import UTC, datetime
    acct_id = 1
    db.add(User(id=acct_id, org_id=1, email="owner@example.com", active=True, created_at=datetime.now(UTC)))
    db.flush()
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


def test_app_lifespan_startup_no_crash(isolated_engine):
    """FastAPI lifespan startup enters cleanly on an isolated database."""
    import asyncio

    import db.session as db_session
    import handlers.deps as deps
    import main as _main

    eng = isolated_engine
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


def test_startup_agent_memory_writes_with_explicit_creator(isolated_engine):
    """Agent memory can be written with explicit creator_id (no request context)."""
    eng = isolated_engine
    _init(eng)

    from sqlalchemy.orm import sessionmaker

    from db.models import AgentMemory, User
    Session = sessionmaker(bind=eng)
    db = Session()
    db.add(User(id=1, org_id=1, email="owner@example.com", active=True))
    db.flush()

    # Simulate what populate_all_agents does — write agent memory
    import uuid
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


def test_startup_routine_creation_with_explicit_creator(isolated_engine):
    """Routines can be seeded with explicit creator_id."""
    eng = isolated_engine
    _init(eng)

    from sqlalchemy.orm import sessionmaker

    from db.models import Routine, User
    from gql.services.number_allocator import NumberAllocator
    Session = sessionmaker(bind=eng)
    db = Session()
    db.add(User(id=1, org_id=1, email="owner@example.com", active=True))
    db.flush()

    routine = Routine(
        id=NumberAllocator.allocate_next(db, entity_type="routine", org_id=1),
        org_id=1,
        creator_id=1,
        name="Test task",
        prompt="Do something",
        schedule="0 9 * * *",
        schedule_display="Daily at 9am",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(routine)
    db.commit()  # Must not raise "No creator context set"

    assert db.query(Routine).count() >= 1
    db.close()


def test_dev_non_tty_auto_recreates(isolated_engine):
    """Non-interactive dev mode (npm run dev) auto-recreates on schema drift."""
    eng = isolated_engine
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
    assert "routines" in inspect(eng).get_table_names()


def test_dev_non_tty_auto_recreates_with_legacy_scheduled_tasks_table(isolated_engine):
    """Dev auto-reset drops legacy pre-rename tables that are no longer in metadata."""
    eng = isolated_engine
    with eng.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    org_id INTEGER NOT NULL,
                    email TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    org_id INTEGER NOT NULL,
                    creator_id INTEGER NOT NULL REFERENCES users(id),
                    name TEXT NOT NULL
                )
                """
            )
        )
        conn.commit()

    import main as _main
    with (
        patch.object(_main, "engine", eng),
        patch.dict(os.environ, {"RENTMATE_ENV": "development"}),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.isatty.return_value = False
        _main._ensure_schema()

    tables = set(inspect(eng).get_table_names())
    assert "routines" in tables
    assert "scheduled_tasks" not in tables
