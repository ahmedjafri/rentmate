"""Generic agent-stream subscribe endpoint.

Clients that dropped connection mid-run can resume by calling
``GET /api/agent-streams/{stream_id}`` — they'll see the buffered
replay followed by any remaining live events.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from handlers.deps import require_user
from llm.agent_stream import subscribe_and_stream
from llm.streams import stream_registry

router = APIRouter()


@router.get("/agent-streams/{stream_id}")
async def subscribe_to_stream(stream_id: str, request: Request):
    await require_user(request)
    run = stream_registry.get(stream_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Stream not found or expired")
    return subscribe_and_stream(run)
