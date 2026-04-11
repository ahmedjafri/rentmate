import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import Base, User

# Load .env into os.environ so LLM-dependent tests (evals, document extraction)
# pick up credentials without needing the env vars set externally.
_ENV_FILE = Path(__file__).parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))


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
    parser.addoption("--postgres", action="store_true", default=False, help="Run tests against a real Postgres container via testcontainers")


@pytest.fixture(scope="session")
def _pg_engine(request):
    """Session-scoped Postgres engine via testcontainers (only created when --postgres is passed)."""
    if not request.config.getoption("--postgres"):
        yield None
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        eng = create_engine(pg.get_connection_url())
        Base.metadata.create_all(eng)
        yield eng
        eng.dispose()


@pytest.fixture
def engine(request, _pg_engine):
    """Per-test database engine. Uses Postgres when --postgres is passed, otherwise in-memory SQLite."""
    if _pg_engine is not None:
        # Re-use the session-scoped Postgres engine; tables are already created.
        # Per-test isolation is handled by the transaction rollback in the `db` fixture.
        return _pg_engine

    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def Session(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


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
        "handlers.heartbeat.SessionLocal",
        "handlers.scheduler.SessionLocal",
        "main.SessionLocal",
    ):
        try:
            monkeypatch.setattr(target, mock_sl)
        except Exception:
            pass

    yield

    db.close = original_close
