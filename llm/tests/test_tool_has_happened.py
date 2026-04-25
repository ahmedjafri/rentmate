"""Tests for the has_happened LLM tool."""
import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from llm.tools import HasHappenedTool


def _run(tool: HasHappenedTool, **kwargs):
    return json.loads(asyncio.run(tool.execute(**kwargs)))


_FAKE_NOW = datetime(2026, 4, 25, 21, 0, 0, tzinfo=UTC)


@pytest.fixture
def frozen_now():
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _FAKE_NOW if tz is None else _FAKE_NOW.astimezone(tz)

    with patch("llm.tools.time_tools.datetime", _FrozenDatetime):
        yield _FAKE_NOW


def test_past_timestamp_with_offset(frozen_now):
    tool = HasHappenedTool()
    result = _run(tool, timestamp="2026-04-22T14:00:00-07:00")
    assert result["has_happened"] is True
    assert result["delta_seconds"] > 0
    assert result["target_utc"] == "2026-04-22T21:00:00+00:00"
    assert "ago" in result["human_delta"]


def test_future_timestamp(frozen_now):
    tool = HasHappenedTool()
    result = _run(tool, timestamp="2099-01-01T00:00:00Z")
    assert result["has_happened"] is False
    assert result["delta_seconds"] < 0
    assert "from now" in result["human_delta"]


def test_z_suffix_parsed_as_utc(frozen_now):
    tool = HasHappenedTool()
    result = _run(tool, timestamp="2026-04-22T21:00:00Z")
    assert result["target_utc"] == "2026-04-22T21:00:00+00:00"
    assert result["has_happened"] is True


def test_naive_timestamp_returns_error(frozen_now):
    tool = HasHappenedTool()
    result = _run(tool, timestamp="2026-04-22T14:00:00")
    assert "error" in result
    assert "timezone" in result["error"].lower()


def test_garbage_string_returns_error(frozen_now):
    tool = HasHappenedTool()
    result = _run(tool, timestamp="not a date")
    assert "error" in result


def test_missing_timestamp_returns_error(frozen_now):
    tool = HasHappenedTool()
    result = _run(tool)
    assert "error" in result


def test_empty_timestamp_returns_error(frozen_now):
    tool = HasHappenedTool()
    result = _run(tool, timestamp="   ")
    assert "error" in result


def test_exactly_now_counts_as_happened(frozen_now):
    tool = HasHappenedTool()
    result = _run(tool, timestamp=_FAKE_NOW.isoformat())
    assert result["has_happened"] is True
    assert result["delta_seconds"] == 0


def test_human_delta_singular_vs_plural(frozen_now):
    tool = HasHappenedTool()
    one_hour_ago = "2026-04-25T20:00:00Z"
    result = _run(tool, timestamp=one_hour_ago)
    assert result["human_delta"] == "1 hour ago"

    two_hours_ago = "2026-04-25T19:00:00Z"
    result = _run(tool, timestamp=two_hours_ago)
    assert result["human_delta"] == "2 hours ago"
