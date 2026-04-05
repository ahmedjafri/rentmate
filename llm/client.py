"""HTTP client for the hosted RentMate agent service.

When ``RENTMATE_AGENT_URL`` is set, agent calls go to the hosted service.
Otherwise, falls back to the local nanobot agent for development.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx


AGENT_URL = os.getenv("RENTMATE_AGENT_URL")  # e.g. https://agent.rentmate.com


@dataclass
class AgentResponse:
    reply: str
    side_effects: list[dict] = field(default_factory=list)


async def call_agent(
    agent_id: str,
    session_key: str,
    messages: list[dict],
    on_progress: Optional[Callable] = None,
    account_context: dict[str, Any] | None = None,
) -> AgentResponse:
    """Call the agent and return its response.

    If ``RENTMATE_AGENT_URL`` is configured, sends an HTTP request to the
    hosted service.  Otherwise, falls back to the local nanobot agent.
    """
    if not AGENT_URL:
        return await _local_fallback(agent_id, session_key, messages, on_progress)

    stream = on_progress is not None
    payload = {
        "agent_id": agent_id,
        "session_key": session_key,
        "messages": messages,
        "stream": stream,
    }
    if account_context:
        payload["account_context"] = account_context

    if stream:
        return await _stream_request(payload, on_progress)
    else:
        return await _sync_request(payload)


async def _stream_request(
    payload: dict,
    on_progress: Callable,
) -> AgentResponse:
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        async with client.stream("POST", f"{AGENT_URL}/v1/agent/chat", json=payload) as resp:
            resp.raise_for_status()
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    if event["type"] == "progress":
                        await on_progress(event.get("text", ""), tool_hint=event.get("tool_hint"))
                    elif event["type"] == "done":
                        return AgentResponse(
                            reply=event["reply"],
                            side_effects=event.get("side_effects", []),
                        )
                    elif event["type"] == "error":
                        raise RuntimeError(event.get("message", "Agent error"))
    raise RuntimeError("Agent stream ended without done event")


async def _sync_request(payload: dict) -> AgentResponse:
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        resp = await client.post(f"{AGENT_URL}/v1/agent/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return AgentResponse(
            reply=data["reply"],
            side_effects=data.get("side_effects", []),
        )


async def _local_fallback(
    agent_id: str,
    session_key: str,
    messages: list[dict],
    on_progress: Optional[Callable] = None,
) -> AgentResponse:
    """Run the agent locally via nanobot (dev mode)."""
    from handlers.chat import chat_with_agent
    from llm.tools import pending_suggestion_messages

    token = pending_suggestion_messages.set([])
    try:
        reply = await chat_with_agent(agent_id, session_key, messages, on_progress)
        # Convert pending suggestion messages into side-effects so the caller
        # gets a uniform interface regardless of local vs hosted execution.
        side_effects = []
        for pending in (pending_suggestion_messages.get() or []):
            side_effects.append({
                "type": "suggestion_message",
                **pending,
            })
        return AgentResponse(reply=reply, side_effects=side_effects)
    finally:
        pending_suggestion_messages.reset(token)
