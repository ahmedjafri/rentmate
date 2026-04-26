"""Time-related primitive tools for the RentMate agent."""
import json
from datetime import UTC, datetime
from typing import Any

from llm.tools._common import Tool, ToolMode


def _format_human_delta(delta_seconds: int) -> str:
    abs_s = abs(delta_seconds)
    suffix = "ago" if delta_seconds >= 0 else "from now"
    if abs_s < 60:
        n, unit = abs_s, "second"
    elif abs_s < 3600:
        n, unit = abs_s // 60, "minute"
    elif abs_s < 86400:
        n, unit = abs_s // 3600, "hour"
    elif abs_s < 86400 * 30:
        n, unit = abs_s // 86400, "day"
    elif abs_s < 86400 * 365:
        n, unit = abs_s // (86400 * 30), "month"
    else:
        n, unit = abs_s // (86400 * 365), "year"
    plural = "s" if n != 1 else ""
    return f"{n} {unit}{plural} {suffix}"


class HasHappenedTool(Tool):
    """Check whether a given timestamp is in the past relative to "now"."""

    mode = ToolMode.READ_ONLY

    @property
    def name(self) -> str:
        return "has_happened"

    @property
    def description(self) -> str:
        return (
            "Check whether a given ISO 8601 timestamp has already happened. "
            "Useful for reasoning about scheduled appointments, deadlines, or follow-up windows. "
            "The timestamp MUST include a timezone offset (e.g. '2026-04-22T14:00:00-07:00' or "
            "'2026-04-22T21:00:00Z'). Returns has_happened (bool), now_utc, target_utc, "
            "delta_seconds (positive if past, negative if future), and a human_delta description. "
            "This tool does not parse natural language — convert phrases like 'Wednesday 2pm' to "
            "ISO yourself before calling, using the relevant property/account timezone."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "timestamp": {
                    "type": "string",
                    "description": (
                        "ISO 8601 timestamp with timezone offset, e.g. "
                        "'2026-04-22T14:00:00-07:00' or '2026-04-22T21:00:00Z'."
                    ),
                },
            },
            "required": ["timestamp"],
        }

    async def execute(self, **kwargs: Any) -> str:
        raw = kwargs.get("timestamp")
        if not isinstance(raw, str) or not raw.strip():
            return json.dumps({
                "error": "Missing 'timestamp'. Provide an ISO 8601 string with timezone offset.",
            })

        try:
            target = datetime.fromisoformat(raw.strip())
        except ValueError as e:
            return json.dumps({
                "error": f"Could not parse timestamp: {e}. Use ISO 8601 with timezone offset.",
            })

        if target.tzinfo is None:
            return json.dumps({
                "error": (
                    "Timestamp is missing a timezone. Include an offset (e.g. '-07:00') or 'Z' "
                    "for UTC."
                ),
            })

        now = datetime.now(UTC)
        target_utc = target.astimezone(UTC)
        delta_seconds = int((now - target_utc).total_seconds())

        return json.dumps({
            "has_happened": delta_seconds >= 0,
            "now_utc": now.isoformat(),
            "target_utc": target_utc.isoformat(),
            "delta_seconds": delta_seconds,
            "human_delta": _format_human_delta(delta_seconds),
        })
