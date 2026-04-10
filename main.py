import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect as sa_inspect
from strawberry.fastapi import GraphQLRouter

from db.models import Base
from gql.schema import schema
from handlers import (
    auth,
    chat,
    dev,
    documents,
    settings,
    tenant_invite,
    tenant_portal,
    vendor_invite,
    vendor_portal,
)
from handlers.deps import SessionLocal, engine
from handlers.scheduler import router as scheduler_router
from handlers.settings import load_integrations
from llm.registry import agent_registry
from memory_watchdog import set_memory_backstop, start_memory_monitor

_HERE = Path(__file__).parent
_DIST = _HERE / "www" / "rentmate-ui" / "dist"

# ─── logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    force=True,
)
_gql_logger = logging.getLogger("rentmate.gql")

# ─── database ────────────────────────────────────────────────────────────────


def _ensure_schema():
    """Manage DB schema based on environment.

    Development (RENTMATE_ENV=development): auto-recreate if schema drifted.
    Production (default): require explicit `alembic upgrade head`.
    """
    is_dev = os.getenv("RENTMATE_ENV") == "development"
    inspector = sa_inspect(engine)
    existing_tables = set(inspector.get_table_names())
    model_tables = set(Base.metadata.tables.keys())

    if not existing_tables or existing_tables == {"alembic_version"}:
        Base.metadata.create_all(engine)
        return

    # Check for schema drift (missing tables or columns)
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
        print("     [m] Run alembic migrations (poetry run alembic upgrade head)")
        print("     [q] Quit\n")
        try:
            choice = input("   Choice [w/m/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "q"
        if choice == "w":
            print("   Wiping and recreating database...")
            Base.metadata.drop_all(engine)
            Base.metadata.create_all(engine)
        elif choice == "m":
            import subprocess
            result = subprocess.run(
                ["poetry", "run", "alembic", "upgrade", "head"],
                cwd=os.path.dirname(__file__) or ".",
            )
            if result.returncode != 0:
                print("   Migration failed. Please fix and retry.")
                raise SystemExit(1)
            print("   Migrations applied successfully.")
        else:
            print("   Aborting.")
            raise SystemExit(0)
    elif is_dev:
        # Non-interactive dev mode (e.g. npm run dev) — auto-recreate
        print("   Schema drift detected — auto-recreating database (dev mode)...")
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    else:
        print("ERROR: Database schema is out of date.")
        print("Run: poetry run alembic upgrade head")
        raise SystemExit(1)

# ─── GraphQL ─────────────────────────────────────────────────────────────────

async def get_context(request: Request):
    from backends.local_auth import set_request_context
    from backends.wire import auth_backend
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "").strip()
    if not token:
        return {"user": None, "db_session": request.state.db_session}
    try:
        user = await auth_backend.validate_token(token)
        # Set request-scoped context so query filters resolve creator_id
        account_id = user.get("account_id")
        if account_id is not None:
            set_request_context(account_id=account_id)
        return {"user": user, "db_session": request.state.db_session}
    except Exception as e:
        print(f"Invalid token, error: {e}")
        return {"user": None, "db_session": request.state.db_session}


graphql_app = GraphQLRouter(schema, context_getter=get_context)

# ─── lifecycle ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── startup ──────────────────────────────────────────────────────────────

    # Run DB init and nanobot gateway startup in parallel (both are blocking I/O / imports)
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

    # Populate os.environ from DB-stored settings (must run after DB init)
    from gql.services.settings_service import load_agent_integrations_into_env, load_llm_into_env
    load_llm_into_env()
    load_agent_integrations_into_env()

    db = SessionLocal()
    try:
        # Set startup context if an account exists (first login creates the account)
        from backends.local_auth import set_request_context
        from db.models import Account
        acct = db.query(Account).first()
        if acct:
            set_request_context(account_id=acct.id)
            agent_registry.populate_all_agents(db)
        # Migrate vendors: ensure all have a short portal_token
        from db.models import ExternalContact as _EC
        from gql.services.vendor_service import VendorService as _VS
        _migrated = 0
        for v in db.query(_EC).all():
            if not (v.extra or {}).get("portal_token"):
                _VS.ensure_portal_token(db, v)
                _migrated += 1
        if _migrated:
            db.commit()
            print(f"Migrated {_migrated} vendor(s) to portal tokens")
        from db.models import Document as DocModel
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

    # Seed default scheduled tasks if none exist
    from handlers.scheduler import scheduler_loop, seed_default_tasks
    seed_default_tasks()

    # Background loops
    asyncio.create_task(scheduler_loop())
    from handlers.heartbeat import heartbeat_loop
    asyncio.create_task(heartbeat_loop())

    if os.getenv("GMAIL_CLIENT_ID"):
        asyncio.create_task(_gmail_poll_loop())
        print("Gmail polling enabled")

    # Quo SMS poller: primary channel locally, backup in production
    from handlers.quo_poller import quo_poll_loop
    asyncio.create_task(quo_poll_loop())

    # Memory watchdog: dump heap and exit if RSS exceeds 8GB
    _data_dir = os.getenv("RENTMATE_DATA_DIR", "./data")
    set_memory_backstop()
    start_memory_monitor(_data_dir)

    yield

    # ── shutdown ─────────────────────────────────────────────────────────────
    agent_registry.stop_gateway()

# ─── app ─────────────────────────────────────────────────────────────────────

app = FastAPI(lifespan=lifespan)
app.include_router(graphql_app, prefix="/graphql")
app.include_router(auth.router)
app.include_router(settings.router)
# automations router removed — replaced by scheduled tasks
app.include_router(documents.router, prefix="/api")
app.include_router(chat.router)
app.include_router(scheduler_router, prefix="/api")
app.include_router(dev.router, prefix="/dev")
app.include_router(vendor_invite.router)
app.include_router(vendor_portal.router)
app.include_router(tenant_invite.router)
app.include_router(tenant_portal.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://app.tenantcloud.com",
        "https://rentmate.io",
"http://localhost:5173",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ─── middleware ───────────────────────────────────────────────────────────────


@app.middleware("http")
async def cache_control_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/"):
        # Vite content-hashes these filenames — safe to cache forever
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@app.middleware("http")
async def db_session_middleware(request: Request, call_next):
    from backends.local_auth import _current_account_id

    # Clear account context at the start of every request to prevent
    # stale values from a previous request leaking into this one.
    _current_account_id.set(None)

    request.state.db_session = SessionLocal()
    try:
        response = await call_next(request)
    finally:
        request.state.db_session.close()
        # Don't reset context here — streaming responses (SSE) continue
        # after the middleware returns. The next request's set(None) at
        # the top handles cleanup.
    return response


def _gql_op_name(payload: dict) -> str:
    name = payload.get("operationName")
    if name:
        return name
    query = payload.get("query", "")
    m = re.match(r"\s*(query|mutation|subscription)\s+(\w+)", query)
    if m:
        return f"{m.group(2)} ({m.group(1)})"
    m2 = re.search(r"\{\s*(\w+)", query)
    return m2.group(1) if m2 else "anonymous"


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

# ─── Gmail polling ───────────────────────────────────────────────────────────

async def _gmail_poll_loop():
    """Poll Gmail every 60 seconds for unread tenant emails and route them to tasks."""
    import asyncio as _asyncio
    while True:
        try:
            await _asyncio.sleep(60)
            await _asyncio.to_thread(_handle_gmail_batch)
        except Exception as e:
            print(f"[gmail-poll] Error: {e}")


def _handle_gmail_batch():
    """Synchronous handler for a single Gmail poll cycle."""

    from sqlalchemy import func

    from backends.gmail import GmailClient
    from db.lib import route_inbound_to_task
    from db.models import Message, MessageType, ParticipantType, Tenant

    gmail = GmailClient()
    try:
        emails = gmail.poll_unread()
    except Exception as e:
        print(f"[gmail-poll] poll_unread failed: {e}")
        return

    if not emails:
        return

    db = SessionLocal()
    try:
        for email in emails:
            from_raw = email.get("from_address", "")
            # Extract plain email address from "Name <addr>" format
            import re as _re
            match = _re.search(r"<([^>]+)>", from_raw)
            from_addr = match.group(1) if match else from_raw.strip()

            tenant = (
                db.query(Tenant)
                .filter(func.lower(Tenant.email) == from_addr.lower())
                .first()
            )
            if not tenant:
                print(f"[gmail-poll] Unknown sender {from_addr!r} — skipping")
                continue

            sender_meta = {
                "source": "gmail",
                "from_address": from_addr,
                "to_address": os.getenv("GMAIL_SENDER_ADDRESS", ""),
                "subject": email.get("subject", ""),
                "thread_id": email.get("thread_id"),
                "gmail_message_id": email.get("message_id"),
            }

            conv, msg = route_inbound_to_task(
                db,
                tenant=tenant,
                body=email.get("body_plain", ""),
                channel_type="email",
                sender_meta=sender_meta,
            )
            db.commit()

            # Run agent and send reply
            import asyncio as _asyncio
            loop = _asyncio.new_event_loop()
            try:
                reply = _run_agent_for_task(db, conv, email.get("body_plain", ""))
                if reply:
                    # Persist AI reply
                    import uuid as _uuid2
                    from datetime import UTC as _UTC2, datetime as _dt2
                    ai_msg = Message(
                        id=str(_uuid2.uuid4()),
                        conversation_id=conv.id,
                        sender_type=ParticipantType.ACCOUNT_USER,
                        body=reply,
                        message_type=MessageType.MESSAGE,
                        sender_name="RentMate",
                        is_ai=True,
                        sent_at=_dt2.now(_UTC2),
                    )
                    db.add(ai_msg)
                    db.commit()
                    # Send email reply
                    gmail.send_reply(
                        to=from_addr,
                        subject=email.get("subject", ""),
                        body=reply,
                        thread_id=email.get("thread_id"),
                    )
            except Exception as e:
                print(f"[gmail-poll] Agent/reply failed for tenant {tenant.id}: {e}")
            finally:
                loop.close()
    finally:
        db.close()


def _run_agent_for_task(db, conv, latest_body: str) -> str:
    """Run the agent synchronously for a task and return its reply text."""
    import asyncio as _asyncio

    from llm.client import call_agent
    from llm.context import build_task_context
    from llm.registry import agent_registry

    context = build_task_context(db, conv.id)
    from db.lib import get_conversation_with_messages
    full_conv = get_conversation_with_messages(db, conv.id)
    msgs = sorted(full_conv.messages, key=lambda m: m.sent_at)
    history_msgs = msgs[:-1][-20:]
    messages = [{"role": "system", "content": context}]
    for m in history_msgs:
        role = "assistant" if m.is_ai else "user"
        messages.append({"role": role, "content": m.body or ""})
    messages.append({"role": "user", "content": latest_body})

    agent_id = agent_registry.ensure_agent(str(acct.id), db)
    session_key = f"email:{conv.id}"

    loop = _asyncio.new_event_loop()
    try:
        resp = loop.run_until_complete(call_agent(agent_id, session_key=session_key, messages=messages))
        return resp.reply
    finally:
        loop.close()


# ─── health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    db_status = "connected"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        db_status = str(e)

    gateway_healthy = agent_registry.is_healthy()

    overall = "healthy" if db_status == "connected" and gateway_healthy else "degraded"
    return {
        "status": overall,
        "database": db_status,
        "nanobot_agent": "healthy" if gateway_healthy else "unavailable",
    }

# ─── static files + SPA catch-all (must be last) ─────────────────────────────

if (_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")


@app.get("/{full_path:path}")
async def serve_react_app(full_path: str):
    index = _DIST / "index.html"
    if not index.exists():
        return {"status": "frontend not built"}
    return FileResponse(str(index), headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="RentMate server")
    parser.add_argument("--data-dir", default=None, metavar="PATH",
                        help="Path to data directory (default: ./data)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    if args.data_dir:
        os.environ["RENTMATE_DATA_DIR"] = args.data_dir

    uvicorn.run("main:app", host=args.host, port=args.port,
                reload=args.reload, log_level=args.log_level)
