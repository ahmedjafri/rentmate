import asyncio
import json
import logging
import os
import re
import shlex
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import MetaData
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.exc import SQLAlchemyError
from strawberry.fastapi import GraphQLRouter

from db.models import Base
from gql.schema import schema
from handlers import (
    chat,
    data_portability,
    dev,
    documents,
    notifications,
    settings,
)
from handlers.portals import tenant_invite, tenant_portal, vendor_invite, vendor_portal
from handlers.deps import SessionLocal, engine
from handlers.routines import router as routine_router
from handlers.streams import router as streams_router
from handlers.task_review import router as task_review_router
from handlers.settings import load_integrations
from llm.registry import agent_registry
from memory_watchdog import set_memory_backstop, start_memory_monitor

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_DIST = _PACKAGE_ROOT / "www" / "rentmate-ui" / "dist"
_DEFAULT_SCHEMA_MIGRATE_COMMAND = ["poetry", "run", "alembic", "upgrade", "head"]
_SCHEMA_MIGRATE_COMMANDS = [_DEFAULT_SCHEMA_MIGRATE_COMMAND]
_SCHEMA_MIGRATE_CWD = _PACKAGE_ROOT
_DEV_BOOTSTRAP_EMAIL = "test@test.com"
_DEV_BOOTSTRAP_PASSWORD = "test"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    force=True,
)
_gql_logger = logging.getLogger("rentmate.gql")


def _reset_dev_schema() -> None:
    """Drop the live schema, including legacy tables missing from current metadata."""
    live_metadata = MetaData()
    live_metadata.reflect(bind=engine)
    if live_metadata.tables:
        live_metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _ensure_schema():
    """Manage DB schema based on environment."""
    is_dev = os.getenv("RENTMATE_ENV") == "development"
    startup_check = os.getenv("STARTUP_CHECK", "").strip().lower()
    if startup_check in {"skip", "0", "false", "off"}:
        print("Skipping schema startup check.")
        return
    inspector = sa_inspect(engine)
    existing_tables = set(inspector.get_table_names())
    model_tables = set(Base.metadata.tables.keys())

    if not existing_tables or existing_tables == {"alembic_version"}:
        Base.metadata.create_all(engine)
        return

    needs_update = False
    for table_name in model_tables:
        if table_name not in existing_tables:
            needs_update = True
            break
        existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
        model_cols = {c.name for c in Base.metadata.tables[table_name].columns}
        if not model_cols.issubset(existing_cols):
            needs_update = True
            break

    if not needs_update:
        return

    import sys

    is_tty = sys.stdin.isatty()

    if is_dev and is_tty:
        print("\n⚠  Schema drift detected — database doesn't match models.")
        print("   Options:")
        print("     [w] Wipe database and recreate (data will be lost)")
        if len(_SCHEMA_MIGRATE_COMMANDS) == 1:
            migration_label = shlex.join(_SCHEMA_MIGRATE_COMMANDS[0])
        else:
            migration_label = " then ".join(shlex.join(command) for command in _SCHEMA_MIGRATE_COMMANDS)
        print(f"     [m] Run alembic migrations ({migration_label})")
        print("     [q] Quit\n")
        try:
            choice = input("   Choice [w/m/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "q"
        if choice == "w":
            print("   Wiping and recreating database...")
            _reset_dev_schema()
        elif choice == "m":
            import subprocess

            for command in _SCHEMA_MIGRATE_COMMANDS:
                result = subprocess.run(command, cwd=str(_SCHEMA_MIGRATE_CWD))
                if result.returncode != 0:
                    print("   Migration failed. Please fix and retry.")
                    raise SystemExit(1)
            print("   Migrations applied successfully.")
        else:
            print("   Aborting.")
            raise SystemExit(0)
    elif is_dev:
        print("   Schema drift detected — auto-recreating database (dev mode)...")
        _reset_dev_schema()
    else:
        print("ERROR: Database schema is out of date.")
        for command in _SCHEMA_MIGRATE_COMMANDS:
            print(f"Run: {shlex.join(command)}")
        raise SystemExit(1)


def _repair_enum_rows() -> None:
    """Repair known bad lowercase enum rows written by older code paths."""
    startup_check = os.getenv("STARTUP_CHECK", "").strip().lower()
    if startup_check in {"skip", "0", "false", "off"}:
        print("Skipping enum row repair startup check.")
        return
    updates = {
        "tasks": "urgency",
        "suggestions": "urgency",
    }
    normalized = {
        "low": "LOW",
        "medium": "MEDIUM",
        "high": "HIGH",
        "critical": "CRITICAL",
    }

    try:
        with engine.begin() as conn:
            existing_tables = set(sa_inspect(engine).get_table_names())
            for table_name, column_name in updates.items():
                if table_name not in existing_tables:
                    continue
                for bad_value, good_value in normalized.items():
                    if engine.dialect.name == "postgresql":
                        conn.execute(
                            text(
                                f"UPDATE {table_name} "
                                f"SET {column_name} = CAST(:good AS urgency_enum) "
                                f"WHERE CAST({column_name} AS TEXT) = :bad"
                            ),
                            {"good": good_value, "bad": bad_value},
                        )
                    else:
                        conn.execute(
                            text(f"UPDATE {table_name} SET {column_name} = :good WHERE {column_name} = :bad"),
                            {"good": good_value, "bad": bad_value},
                        )
    except SQLAlchemyError as exc:
        print(f"Skipping enum row repair because database is unavailable: {exc}")


def _ensure_dev_bootstrap_account(db) -> None:
    """Create the default local-dev owner account if the database has no users."""
    if os.getenv("RENTMATE_ENV") != "development":
        return

    from backends.local_auth import _hash_password
    from db.models import User

    if db.query(User).first():
        return

    acct = User(
        email=_DEV_BOOTSTRAP_EMAIL,
        password_hash=_hash_password(_DEV_BOOTSTRAP_PASSWORD),
        active=True,
        user_type="account",
    )
    db.add(acct)
    db.flush()
    db.commit()
    print(f"Dev bootstrap account created: {_DEV_BOOTSTRAP_EMAIL}")


async def get_context(request: Request):
    from backends.local_auth import set_request_context
    from backends.wire import auth_backend

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "").strip()
    if not token:
        return {"user": None, "db_session": request.state.db_session}
    try:
        user = await auth_backend.validate_token(token, db=request.state.db_session)
        account_id = user.get("account_id")
        if account_id is not None:
            set_request_context(account_id=account_id, org_id=user.get("org_id"))
        return {"user": user, "db_session": request.state.db_session}
    except Exception as exc:
        _gql_logger.info("Invalid token, error: %s", exc)
        return {"user": None, "db_session": request.state.db_session}


graphql_app = GraphQLRouter(schema, context_getter=get_context)


def create_app(
    *,
    pre_request_hook=None,
    allow_origins: list[str] | None = None,
    allow_origin_regex: str | None = None,
    dist_root: Path | None = None,
    schema_migrate_command: list[str] | None = None,
    schema_migrate_commands: list[list[str]] | None = None,
    schema_migrate_cwd: Path | None = None,
) -> FastAPI:
    global _SCHEMA_MIGRATE_COMMANDS, _SCHEMA_MIGRATE_CWD
    if schema_migrate_commands is not None:
        _SCHEMA_MIGRATE_COMMANDS = schema_migrate_commands
    elif schema_migrate_command is not None:
        _SCHEMA_MIGRATE_COMMANDS = [schema_migrate_command]
    else:
        _SCHEMA_MIGRATE_COMMANDS = [_DEFAULT_SCHEMA_MIGRATE_COMMAND]
    if schema_migrate_cwd is not None:
        _SCHEMA_MIGRATE_CWD = schema_migrate_cwd

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        startup_check = os.getenv("STARTUP_CHECK", "").strip().lower()
        skip_db_bootstrap = startup_check in {"skip", "0", "false", "off"}

        async def _init_db():
            await asyncio.to_thread(_ensure_schema)
            print("Database schema ready")

        try:
            await asyncio.gather(
                _init_db(),
                asyncio.to_thread(agent_registry.start_gateway),
            )
        except (SystemExit, KeyboardInterrupt):
            raise
        except (asyncio.CancelledError, Exception) as exc:
            print(f"Startup failed: {exc}" if not isinstance(exc, asyncio.CancelledError) else "Startup cancelled")
            import sys

            sys.exit(1)

        _repair_enum_rows()

        from gql.services.settings_service import load_agent_integrations_into_env, load_llm_into_env

        load_llm_into_env()
        load_agent_integrations_into_env()

        if skip_db_bootstrap:
            print("Skipping DB-dependent startup tasks.")
        else:
            db = SessionLocal()
            try:
                from backends.local_auth import set_request_context
                from db.models import Document as DocModel, User

                _ensure_dev_bootstrap_account(db)
                acct = db.query(User).first()
                if acct:
                    set_request_context(account_id=acct.id, org_id=acct.org_id)
                    if os.getenv("RENTMATE_ENV") == "development":
                        try:
                            from demo.seed import seed_if_needed
                            if seed_if_needed(db):
                                db.commit()
                                print("Dev seed data created.")
                        except Exception as exc:
                            db.rollback()
                            print(f"Dev seed failed: {exc}")
                stuck = db.query(DocModel).filter(DocModel.status.in_(["pending", "processing"])).all()
                for doc in stuck:
                    doc.status = "pending"
                    doc.progress = None
                if stuck:
                    db.commit()
                    print(f"Re-queuing {len(stuck)} stuck document(s)…")
                    from llm.document_processor import process_document

                    for doc in stuck:
                        asyncio.create_task(process_document(doc.id))
            finally:
                db.close()

            await agent_registry.restart_channels_async(load_integrations())

            from handlers.routines import routine_loop, seed_default_routines

            seed_default_routines()
            asyncio.create_task(routine_loop())

            from handlers.reply_scanner import reply_scanner_loop
            from handlers.quo_poller import quo_poll_loop
            from handlers.task_review import task_review_loop

            asyncio.create_task(reply_scanner_loop())
            asyncio.create_task(quo_poll_loop())
            asyncio.create_task(task_review_loop())

            if os.getenv("RENTMATE_DEMO_SIMULATOR") == "1":
                from demo.simulator import simulator_loop
                asyncio.create_task(simulator_loop())
                print("[demo] tenant/vendor simulator enabled")

        data_dir = os.getenv("RENTMATE_DATA_DIR", "./data")
        from backends.local_storage import ensure_runtime_storage_contract

        data_dir, _ = ensure_runtime_storage_contract()
        set_memory_backstop()
        start_memory_monitor(str(data_dir))

        yield

        agent_registry.stop_gateway()

    app = FastAPI(lifespan=lifespan)
    app.include_router(graphql_app, prefix="/graphql")
    app.include_router(settings.router)
    app.include_router(documents.router, prefix="/api")
    app.include_router(chat.router)
    app.include_router(routine_router, prefix="/api")
    app.include_router(task_review_router, prefix="/api")
    app.include_router(notifications.router, prefix="/api")
    app.include_router(streams_router, prefix="/api")
    app.include_router(data_portability.router, prefix="/api")
    app.include_router(dev.router, prefix="/dev")
    app.include_router(vendor_invite.router)
    app.include_router(vendor_portal.router)
    app.include_router(tenant_invite.router)
    app.include_router(tenant_portal.router)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins
        or [
            "https://app.tenantcloud.com",
            "https://rentmate.io",
            "http://localhost:5173",
            "http://localhost:8080",
        ],
        allow_origin_regex=allow_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.middleware("http")
    async def cache_control_middleware(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.middleware("http")
    async def db_session_middleware(request: Request, call_next):
        from backends.local_auth import _current_account_id, _current_org_id

        _current_account_id.set(None)
        _current_org_id.set(None)

        request.state.db_session = SessionLocal()
        try:
            if pre_request_hook:
                pre_request_hook(request, request.state.db_session)
            response = await call_next(request)
        finally:
            request.state.db_session.close()
        return response

    @app.middleware("http")
    async def graphql_logging_middleware(request: Request, call_next):
        if request.url.path != "/graphql" or request.method != "POST":
            return await call_next(request)
        body_bytes = await request.body()
        try:
            op = _gql_op_name(json.loads(body_bytes))
        except Exception:
            op = "?"
        response = await call_next(request)
        _gql_logger.info("%-45s → %s", op, response.status_code)
        return response

    @app.get("/health")
    async def health_check():
        db_status = "connected"
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            db_status = str(exc)

        gateway_healthy = agent_registry.is_healthy()
        overall = "healthy" if db_status == "connected" and gateway_healthy else "degraded"
        return {
            "status": overall,
            "database": db_status,
            "nanobot_agent": "healthy" if gateway_healthy else "unavailable",
        }

    resolved_dist = dist_root or _DIST
    if (resolved_dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(resolved_dist / "assets")), name="assets")

    _BACKEND_ROUTE_PREFIXES = (
        "api",
        "auth",
        "graphql",
        "health",
        "chat",
        "automations",
        "action-items",
        "dev",
        "onboarding",
        "quo-webhook",
        "settings",
    )

    @app.get("/{full_path:path}")
    async def serve_react_app(full_path: str):
        if full_path.split("/", 1)[0] in _BACKEND_ROUTE_PREFIXES:
            raise HTTPException(status_code=404, detail="Not found")
        index = resolved_dist / "index.html"
        if not index.exists():
            return {"status": "frontend not built"}
        return FileResponse(str(index), headers={"Cache-Control": "no-cache"})

    return app


def _gql_op_name(payload: dict) -> str:
    name = payload.get("operationName")
    if name:
        return name
    query = payload.get("query", "")
    match = re.match(r"\s*(query|mutation|subscription)\s+(\w+)", query)
    if match:
        return f"{match.group(2)} ({match.group(1)})"
    fallback = re.search(r"\{\s*(\w+)", query)
    return fallback.group(1) if fallback else "anonymous"


app = create_app()
lifespan = app.router.lifespan_context

__all__ = [
    "SessionLocal",
    "_ensure_schema",
    "_reset_dev_schema",
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
