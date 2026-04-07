import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

_HERE = Path(__file__).parent
_DIST = _HERE / "www" / "rentmate-ui" / "dist"

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from strawberry.fastapi import GraphQLRouter

from db.models import Base
from gql.schema import schema
from handlers import (
    auth,
    automations,
    chat,
    dev,
    documents,
    settings,
    tenant_invite,
    tenant_portal,
    vendor_invite,
    vendor_portal,
)
from handlers.deps import SessionLocal, engine, require_user
from handlers.settings import load_integrations, read_env_file
from llm.registry import agent_registry

# ─── logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    force=True,
)
_gql_logger = logging.getLogger("rentmate.gql")

# ─── database ────────────────────────────────────────────────────────────────

_MIGRATE_COLS = [
    ("documents",      "sha256_checksum",   "TEXT"),
    ("documents",      "suggestion_states",  "TEXT"),
    ("documents",      "confirmed_at",       "DATETIME"),
    ("documents",      "extraction_meta",    "TEXT"),
    ("conversations",  "conversation_type",        "VARCHAR(20)"),
    ("conversations",  "parent_conversation_id",   "VARCHAR(36)"),
    ("conversations",  "ai_initiated",             "BOOLEAN NOT NULL DEFAULT 0"),
    ("conversations",  "extra",                    "TEXT"),
    ("tasks",          "task_number",              "INTEGER"),
    ("tasks",          "ai_conversation_id",       "VARCHAR(36)"),
    ("tasks",          "parent_conversation_id",   "VARCHAR(36)"),
    ("tasks",          "external_conversation_id", "VARCHAR(36)"),
    ("tasks",          "resolved_at",              "TIMESTAMP"),
    ("tasks",          "steps",                    "TEXT"),
    ("messages",       "message_type",       "VARCHAR(20)"),
    ("messages",       "sender_name",        "VARCHAR(255)"),
    ("messages",       "is_ai",              "BOOLEAN NOT NULL DEFAULT 0"),
    ("messages",       "draft_reply",        "TEXT"),
    ("messages",       "approval_status",    "VARCHAR(20)"),
    ("messages",       "related_task_ids",   "TEXT"),
    ("leases",         "payment_status",     "VARCHAR(20) DEFAULT 'current'"),
    ("properties",     "property_type",      "VARCHAR(20) DEFAULT 'multi_family'"),
    ("properties",     "source",             "VARCHAR(20)"),
    ("properties",     "context",            "TEXT"),
    ("units",          "context",            "TEXT"),
    ("tenants",        "context",            "TEXT"),
    ("external_contacts", "context",         "TEXT"),
    ("tasks",             "context",         "TEXT"),
]


def _migrate_schema():
    """Add columns that may be missing from older schema versions."""
    new_cols = _MIGRATE_COLS
    tables = {t for t, _, _ in new_cols}
    with engine.connect() as conn:
        # Detect DB dialect and query existing columns in one pass per table
        dialect = engine.dialect.name
        existing: set[tuple[str, str]] = set()
        if dialect == "sqlite":
            present_tables: set[str] = set()
            try:
                rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
                present_tables = {row[0] for row in rows}
            except Exception:
                pass
            for table in tables:
                if table not in present_tables:
                    # Mark all columns as already "existing" so we don't try ALTER
                    for _, col, _ in new_cols:
                        existing.add((table, col))
                    continue
                try:
                    rows = conn.execute(text(f"PRAGMA table_info({table})"))
                    for row in rows:
                        existing.add((table, row[1]))
                except Exception:
                    pass
        else:
            result = conn.execute(text("""
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                AND table_name = ANY(:tables)
            """), {"tables": list(tables)})
            existing = {(row[0], row[1]) for row in result}

        for table, col, typ in new_cols:
            if (table, col) not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
                conn.commit()

# ─── GraphQL ─────────────────────────────────────────────────────────────────

async def get_context(request: Request):
    from backends.wire import auth_backend
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "").strip()
    if not token:
        return {"user": None, "db_session": request.state.db_session}
    try:
        user = await auth_backend.validate_token(token)
        return {"user": user, "db_session": request.state.db_session}
    except Exception as e:
        print(f"Invalid token, error: {e}")
        return {"user": None, "db_session": request.state.db_session}


graphql_app = GraphQLRouter(schema, context_getter=get_context)

# ─── lifecycle ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── startup ──────────────────────────────────────────────────────────────
    for key, value in read_env_file().items():
        if not os.environ.get(key):  # set if missing or empty
            os.environ[key] = value

    # Run DB init and nanobot gateway startup in parallel (both are blocking I/O / imports)
    async def _init_db():
        await asyncio.to_thread(Base.metadata.create_all, engine)
        await asyncio.to_thread(_migrate_schema)
        print("Database tables created/migrated")

    await asyncio.gather(
        _init_db(),
        asyncio.to_thread(agent_registry.start_gateway),
    )

    db = SessionLocal()
    try:
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

    if os.getenv("RENTMATE_ENV") == "development":
        automations.seed_automations()

    asyncio.create_task(automations.audit_loop())
    asyncio.create_task(automations.heartbeat_loop())

    if os.getenv("GMAIL_CLIENT_ID"):
        asyncio.create_task(_gmail_poll_loop())
        print("Gmail polling enabled")

    # Quo SMS poller: primary channel locally, backup in production
    from handlers.quo_poller import quo_poll_loop
    asyncio.create_task(quo_poll_loop())

    yield

    # ── shutdown ─────────────────────────────────────────────────────────────
    agent_registry.stop_gateway()

# ─── app ─────────────────────────────────────────────────────────────────────

app = FastAPI(lifespan=lifespan)
app.include_router(graphql_app, prefix="/graphql")
app.include_router(auth.router)
app.include_router(settings.router)
app.include_router(automations.router)
app.include_router(documents.router, prefix="/api")
app.include_router(chat.router)
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

# ─── middleware ───────────────────────────────────────────────────────────────

@app.middleware("http")
async def db_session_middleware(request: Request, call_next):
    request.state.db_session = SessionLocal()
    try:
        response = await call_next(request)
    finally:
        request.state.db_session.close()
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
    import uuid as _uuid
    from datetime import datetime as _dt

    from sqlalchemy import func

    from backends.gmail import GmailClient
    from db.lib import route_inbound_to_task
    from db.models import Conversation, Message, MessageType, ParticipantType, Tenant

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

    from backends.local_auth import DEFAULT_USER_ID
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

    agent_id = agent_registry.ensure_agent(DEFAULT_USER_ID, db)
    session_key = f"email:{conv.id}"

    loop = _asyncio.new_event_loop()
    try:
        resp = loop.run_until_complete(call_agent(agent_id, session_key, messages))
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
    return FileResponse(str(index))
