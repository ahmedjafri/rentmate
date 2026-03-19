import os
import pytest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from db.models import Base

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


@pytest.fixture
def engine():
    """Fresh in-memory SQLite database per test — guarantees zero cross-test contamination."""
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
        trans.rollback()
        connection.close()
