"""Render the SOUL.md tool list from the live tool registry.

Single source of truth for "what tools the agent has + how the manager
should think about them" lives on the ``Tool`` subclasses
(``description`` + ``category``). This module groups them by category
and emits the markdown that ``{{tools}}`` in SOUL.md expands into.

If you add a new tool: register it in ``llm/dispatch.py::_TOOL_CLASSES``
and SOUL.md picks it up automatically.
"""
from __future__ import annotations

import re

from llm.dispatch import _TOOL_CLASSES
from llm.tools._common import Tool, ToolCategory


_CATEGORY_ORDER = (
    (ToolCategory.READ, "**Read tools** (safe, use freely):"),
    (ToolCategory.IMMEDIATE, "**Immediate tools** (apply directly, no approval needed):"),
    (
        ToolCategory.REVIEW,
        "**Write tools** (queue as suggestions — auto-approved in autonomous mode, otherwise require manager confirmation):",
    ),
)

# Strip duplicate inner whitespace + cap to roughly one sentence so the
# SOUL bullet stays scannable. Tool descriptions sometimes span multiple
# sentences with implementation guidance; the first sentence is almost
# always the one that belongs in SOUL.
_FIRST_SENTENCE_RE = re.compile(r"(.+?[.?!])(?:\s|$)", re.DOTALL)


def _short_description(tool: Tool) -> str:
    text = " ".join((tool.description or "").split())
    if not text:
        return ""
    m = _FIRST_SENTENCE_RE.match(text)
    return (m.group(1) if m else text).strip()


def render_tools_markdown() -> str:
    """Return the markdown block listing every registered tool by category."""
    by_category: dict[ToolCategory, list[Tool]] = {cat: [] for cat, _ in _CATEGORY_ORDER}
    for cls in _TOOL_CLASSES:
        tool = cls()
        by_category.setdefault(tool.category, []).append(tool)

    sections: list[str] = []
    for category, header in _CATEGORY_ORDER:
        bucket = sorted(by_category.get(category, []), key=lambda t: t.name)
        if not bucket:
            continue
        lines = [header]
        for tool in bucket:
            short = _short_description(tool)
            lines.append(f"- `{tool.name}` — {short}" if short else f"- `{tool.name}`")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
