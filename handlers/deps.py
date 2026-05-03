"""Shared dependencies used across all handler modules."""
import json
import re

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from db.session import SessionLocal, engine  # noqa: F401 — re-exported
from integrations.local_auth import set_request_context
from integrations.wire import auth_backend


def get_db(request: Request) -> Session:
    return request.state.db_session


async def require_user(request: Request) -> dict:
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        user = await auth_backend.validate_token(token, db=request.state.db_session)
        # Set request-scoped context for query filters and entity creation
        account_id = user.get("account_id")
        if account_id is not None:
            set_request_context(account_id=account_id, org_id=user.get("org_id"))
        return user
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
