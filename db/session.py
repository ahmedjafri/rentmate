"""Database engine and session factory.

This is the canonical location for SessionLocal and engine.
Import from here — not from handlers.deps.
"""
import os

from sqlalchemy import create_engine, event as _sa_event
from sqlalchemy.orm import scoped_session, sessionmaker

_data_dir = os.getenv("RENTMATE_DATA_DIR", "./data")
DB_PATH = os.getenv("RENTMATE_DB_PATH", f"{_data_dir}/rentmate.db")
os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


@_sa_event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
