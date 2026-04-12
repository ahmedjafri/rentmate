"""Database engine and session factory.

This is the canonical location for SessionLocal and engine.
Import from here — not from handlers.deps.
"""

import os

from sqlalchemy import create_engine, event as _sa_event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import scoped_session, sessionmaker


def _make_engine() -> Engine:
    db_uri = os.getenv("RENTMATE_DB_URI", "").strip()
    if db_uri:
        return create_engine(db_uri)

    data_dir = os.getenv("RENTMATE_DATA_DIR", "./data")
    db_path = os.getenv("RENTMATE_DB_PATH", f"{data_dir}/rentmate.db")
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    return create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})


engine = _make_engine()


@_sa_event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
