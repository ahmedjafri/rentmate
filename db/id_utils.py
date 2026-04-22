from __future__ import annotations

from typing import Any


def normalize_optional_id(value: Any) -> Any:
    """Normalize optional relationship IDs so blank strings become None."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value
