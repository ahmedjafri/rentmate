import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import sessionmaker

from db.models import Base, User

_POSTGRES_CONTAINER = None
_POSTGRES_ADMIN_URL: URL | None = None
_POSTGRES_SHARED_URL: str | None = None

# Load .env into os.environ so LLM-dependent tests (evals, document extraction)
# pick up credentials without needing the env vars set externally.
_ENV_FILE = Path(__file__).parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

os.environ.setdefault("HERMES_HOME", str((Path(__file__).parent / "data" / "test-hermes").resolve()))


@pytest.fixture(autouse=True)
def _set_creator_context():
    """Set a default creator context for tests so entity creation works."""
    from backends.local_auth import reset_request_context, set_request_context
    tokens = set_request_context(account_id=1, org_id=1)
    yield
    reset_request_context(tokens)


@pytest.fixture(autouse=True)
def _no_llm_suggestion(request):
    """Suppress generate_task_suggestion for all non-eval tests to prevent
    real LLM calls during unit/integration tests and keep task message counts predictable."""
    if request.node.get_closest_marker("eval"):
        yield
        return
    with patch("llm.suggest.generate_task_suggestion", return_value=None):
        yield


def pytest_addoption(parser):
    parser.addoption("--pdf", action="store", default=None, help="Path to PDF for document extraction tests")


def _build_admin_url(raw_url: str) -> URL:
    url = make_url(raw_url)
    return url.set(database="postgres")


def _create_database(name: str) -> str:
    admin_engine = create_engine(_POSTGRES_ADMIN_URL)
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin_engine.dispose()
    return _POSTGRES_ADMIN_URL.set(database=name).render_as_string(hide_password=False)


def _drop_database(name: str) -> None:
    admin_engine = create_engine(_POSTGRES_ADMIN_URL)
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin_engine.dispose()


def pytest_sessionstart(session):
    from testcontainers.postgres import PostgresContainer

    global _POSTGRES_CONTAINER, _POSTGRES_ADMIN_URL, _POSTGRES_SHARED_URL
    _POSTGRES_CONTAINER = PostgresContainer("postgres:16-alpine")
    _POSTGRES_CONTAINER.start()
    _POSTGRES_ADMIN_URL = _build_admin_url(_POSTGRES_CONTAINER.get_connection_url())
    _POSTGRES_SHARED_URL = _create_database("rentmate_pytest")
    os.environ["RENTMATE_DB_URI"] = _POSTGRES_SHARED_URL
    os.environ.setdefault("DATABASE_URL", _POSTGRES_SHARED_URL)
    eng = create_engine(_POSTGRES_SHARED_URL)
    Base.metadata.create_all(eng)
    eng.dispose()


def pytest_sessionfinish(session, exitstatus):
    global _POSTGRES_CONTAINER
    if _POSTGRES_CONTAINER is not None:
        _drop_database("rentmate_pytest")
        _POSTGRES_CONTAINER.stop()
        _POSTGRES_CONTAINER = None


@pytest.fixture(scope="session")
def engine():
    """Shared Postgres engine for most tests; per-test isolation is transactional."""
    eng = create_engine(_POSTGRES_SHARED_URL)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def Session(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def isolated_engine():
    """Fresh Postgres database for tests that need startup/schema isolation."""
    db_name = f"rentmate_test_{uuid.uuid4().hex}"
    db_url = _create_database(db_name)
    eng = create_engine(db_url)
    try:
        yield eng
    finally:
        eng.dispose()
        _drop_database(db_name)


@pytest.fixture
def db(Session, engine, request):
    """Per-test transaction rollback isolation."""
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    try:
        if getattr(request, "instance", None) is not None:
            setattr(request.instance, "db", session)
        yield session
    finally:
        session.close()
        if trans.is_active and connection.in_transaction():
            trans.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def _seed_current_user(db):
    """Ensure the default authenticated test user exists."""
    user = db.get(User, 1)
    if user is None:
        user = User(
            id=1,
            org_id=1,
            external_id="test-user-1",
            email="test-admin@example.com",
            active=True,
        )
        db.add(user)
        db.flush()
    elif user.external_id != "test-user-1":
        user.external_id = "test-user-1"
        db.flush()

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text(
                """
                SELECT setval(
                    pg_get_serial_sequence('users', 'id'),
                    GREATEST((SELECT COALESCE(MAX(id), 1) FROM users), 1),
                    true
                )
                """
            )
        )
        db.flush()
    return user


@pytest.fixture(autouse=True)
def _isolate_app_sessionlocal(request, monkeypatch):
    """Route app/session-factory access to the per-test DB session when a test uses `db`.

    This prevents handler tests using TestClient(app) from touching the real app DB
    via `main.SessionLocal`, middleware, or modules that import `db.session.SessionLocal`.
    """
    if "db" not in request.fixturenames:
        yield
        return

    db = request.getfixturevalue("db")
    original_close = db.close
    monkeypatch.setattr(db, "close", lambda: None)

    mock_sl = MagicMock(name="SessionLocal")
    mock_sl.return_value = db
    mock_sl.session_factory.return_value = db

    for target in (
        "db.session.SessionLocal",
        "handlers.deps.SessionLocal",
        "handlers.chat.SessionLocal",
        "handlers.reply_scanner.SessionLocal",
        "handlers.routines.SessionLocal",
        "handlers.task_review.SessionLocal",
        "main.SessionLocal",
        "rentmate.app.SessionLocal",
    ):
        try:
            monkeypatch.setattr(target, mock_sl)
        except Exception:
            pass

    yield

    db.close = original_close
