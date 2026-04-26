"""Tests for the personality registry shipped with the demo simulator."""
from __future__ import annotations

from demo.personalities import (
    DEFAULT_PERSONALITY,
    PERSONALITIES,
    Personality,
    get_personality_by_phone,
    is_seeded_phone,
)
from demo.seed import TENANTS, VENDORS


def test_every_seeded_phone_has_a_personality():
    """Adding a tenant or vendor in seed.py without an entry here would
    silently fall back to the default personality — flag that early so
    every demo character has a real voice."""
    seeded_phones = {entry["phone"] for entry in TENANTS + VENDORS}
    missing = seeded_phones - set(PERSONALITIES.keys())
    assert not missing, f"Seeded users missing personalities: {missing}"


def test_get_personality_returns_default_for_unknown_phone():
    out = get_personality_by_phone("+19999999999")
    assert out is DEFAULT_PERSONALITY


def test_get_personality_returns_default_for_none_phone():
    assert get_personality_by_phone(None) is DEFAULT_PERSONALITY
    assert get_personality_by_phone("") is DEFAULT_PERSONALITY


def test_is_seeded_phone():
    assert is_seeded_phone("+14255550101") is True   # Marcus
    assert is_seeded_phone("+19999999999") is False
    assert is_seeded_phone(None) is False


def test_personality_dataclass_is_frozen():
    """Catch accidental mutation — these are constants."""
    p = PERSONALITIES["+14255550101"]
    assert isinstance(p, Personality)
    try:
        p.voice = "changed"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Personality should be frozen")
