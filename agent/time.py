"""Clock helpers for agent runtime and eval simulation."""
from __future__ import annotations

import os
from datetime import UTC, datetime


def current_utc() -> datetime:
    """Return the current UTC time, overridable by eval harnesses."""
    override = os.getenv("RENTMATE_EVAL_NOW_UTC", "").strip()
    if override:
        parsed = datetime.fromisoformat(override.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return datetime.now(UTC)
