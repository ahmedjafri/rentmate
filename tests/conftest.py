import os
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from db.models import Base


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
