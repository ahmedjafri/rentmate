import argparse
import os
from contextlib import asynccontextmanager

import uvicorn

import rentmate.app as _app

SessionLocal = _app.SessionLocal
agent_registry = _app.agent_registry
app = _app.app
asyncio = _app.asyncio
engine = _app.engine
get_context = _app.get_context
set_memory_backstop = _app.set_memory_backstop
start_memory_monitor = _app.start_memory_monitor


def _sync_runtime_globals() -> None:
    _app.SessionLocal = SessionLocal
    _app.agent_registry = agent_registry
    _app.asyncio = asyncio
    _app.engine = engine
    _app.set_memory_backstop = set_memory_backstop
    _app.start_memory_monitor = start_memory_monitor


def _ensure_schema():
    _sync_runtime_globals()
    return _app._ensure_schema()


def _repair_enum_rows():
    _sync_runtime_globals()
    return _app._repair_enum_rows()


def create_app(*args, **kwargs):
    return _app.create_app(*args, **kwargs)


@asynccontextmanager
async def lifespan(app_instance):
    _sync_runtime_globals()
    async with _app.app.router.lifespan_context(app_instance):
        yield


__all__ = [
    "SessionLocal",
    "_ensure_schema",
    "_repair_enum_rows",
    "agent_registry",
    "app",
    "asyncio",
    "create_app",
    "engine",
    "get_context",
    "lifespan",
    "set_memory_backstop",
    "start_memory_monitor",
]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RentMate server")
    parser.add_argument("--data-dir", default=None, metavar="PATH", help="Path to data directory (default: ./data)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    if args.data_dir:
        os.environ["RENTMATE_DATA_DIR"] = args.data_dir

    uvicorn.run("main:app", host=args.host, port=args.port, reload=args.reload, log_level=args.log_level)
