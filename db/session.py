"""Database engine and session factory."""

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import scoped_session, sessionmaker


def _default_db_uri() -> str:
    return os.getenv("RENTMATE_DB_URI", "").strip() or "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/rentmate"


def _make_engine() -> Engine:
    return create_engine(_default_db_uri())


engine = _make_engine()


SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
