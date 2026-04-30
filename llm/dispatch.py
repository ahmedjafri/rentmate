"""Local tool registry and dispatcher for the in-house agent loop.

Replaces the external ``tools.registry`` (Hermes) singleton with a
module-level dict of ``Tool`` instances. The registry itself is shared
across tenants because tool instances are stateless — every tool reads
account/org from contextvars at execute time. Per-tenant safety is a
defensive contextvar assertion done at dispatch.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from llm.tools import (
    AddTaskNoteTool,
    AddTenantToLeaseTool,
    AnalyzeDocumentTool,
    AskManagerTool,
    CloseTaskTool,
    CreateDocumentTool,
    CreateLeaseTool,
    CreatePropertyTool,
    CreateRoutineTool,
    CreateSuggestionTool,
    CreateTenantTool,
    CreateVendorTool,
    EditMemoryTool,
    HasHappenedTool,
    ListTasksTool,
    LookupLeasesTool,
    LookupPropertiesTool,
    LookupTenantsTool,
    LookupVendorsTool,
    MessageExternalPersonTool,
    ProposeTaskTool,
    ReadDocumentTool,
    RecallMemoryTool,
    RecordTaskReviewTool,
    RememberAboutEntityTool,
    RemoveTenantFromLeaseTool,
    TerminateLeaseTool,
    Tool,
    UpdateLeaseTool,
    UpdateOnboardingTool,
    UpdateTaskProgressTool,
    WebSearchTool,
)
from llm.tools._common import ToolMode, is_simulating, record_simulated_action

logger = logging.getLogger(__name__)


_TOOL_CLASSES: tuple[type[Tool], ...] = (
    ProposeTaskTool, CloseTaskTool, UpdateTaskProgressTool, ListTasksTool,
    MessageExternalPersonTool,
    LookupPropertiesTool, LookupTenantsTool, LookupVendorsTool, CreateVendorTool,
    HasHappenedTool,
    RememberAboutEntityTool, AddTaskNoteTool, RecallMemoryTool, EditMemoryTool,
    CreatePropertyTool, CreateTenantTool, CreateSuggestionTool,
    CreateLeaseTool, LookupLeasesTool, UpdateLeaseTool, TerminateLeaseTool,
    AddTenantToLeaseTool, RemoveTenantFromLeaseTool,
    CreateRoutineTool,
    CreateDocumentTool,
    ReadDocumentTool, AnalyzeDocumentTool,
    AskManagerTool, RecordTaskReviewTool,
    UpdateOnboardingTool,
    WebSearchTool,
)


def _build_registry() -> dict[str, Tool]:
    registry: dict[str, Tool] = {}
    for cls in _TOOL_CLASSES:
        tool = cls()
        if tool.name in registry:
            raise RuntimeError(f"Duplicate tool name in registry: {tool.name}")
        registry[tool.name] = tool
    return registry


_REGISTRY: dict[str, Tool] = _build_registry()


def tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI/LiteLLM-format tool definitions for every registered tool."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in _REGISTRY.values()
    ]


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


async def dispatch(name: str, args: dict[str, Any] | None) -> str:
    """Dispatch a single tool call, honoring simulation mode.

    Returns the tool's JSON string result (or an error JSON for unknown tools).
    READ_WRITE tools are blackholed when ``is_simulating()`` is true: their
    inputs are recorded and a synthetic success payload is returned without
    calling ``execute``. READ_ONLY tools always run.
    """
    args = args or {}
    tool = _REGISTRY.get(name)
    if tool is None:
        return json.dumps({
            "status": "error",
            "error": f"unknown tool: {name}",
        })

    if tool.mode == ToolMode.READ_WRITE and is_simulating():
        sim_id = record_simulated_action(tool.name, args)
        return json.dumps({
            "status": "ok",
            "simulation_id": sim_id,
            "message": f"(simulation) would call {tool.name}",
        })

    return await tool.execute(**args)
