"""Shared dependencies used across all handler modules."""
import os
import re
import json

from fastapi import HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker, scoped_session

from backends.wire import auth_backend

_data_dir = os.getenv("RENTMATE_DATA_DIR", "./data")
DB_PATH = os.getenv("RENTMATE_DB_PATH", f"{_data_dir}/rentmate.db")
os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

# Enable WAL mode for better concurrent read/write support
from sqlalchemy import event as _sa_event
@_sa_event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))


def get_db(request: Request) -> Session:
    return request.state.db_session


async def require_user(request: Request) -> dict:
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await auth_backend.validate_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


def extract_json(text: str) -> dict:
    """Extract the first JSON object from text, stripping markdown fences if present."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        return json.loads(m.group(0))
    return json.loads(text)
