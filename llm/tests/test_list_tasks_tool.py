"""Tests for ListTasksTool."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from db.enums import TaskMode, TaskStatus, Urgency
from db.models import Task
from gql.services.number_allocator import NumberAllocator
from llm.tools import ListTasksTool


def _run_tool(tool: ListTasksTool, **kwargs):
    return json.loads(asyncio.run(tool.execute(**kwargs)))


def _make_task(db, *, title: str, status=TaskStatus.ACTIVE, urgency=None,
               property_id: str | None = None, goal: str | None = None,
               creator_id: int = 1):
    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1,
        creator_id=creator_id,
        title=title,
        goal=goal,
        task_status=status,
        task_mode=TaskMode.MANUAL,
        urgency=urgency,
        property_id=property_id,
    )
    db.add(task)
    db.flush()
    return task


def test_list_tasks_defaults_to_active(db):
    _make_task(db, title="Open task A", status=TaskStatus.ACTIVE)
    _make_task(db, title="Open task B", status=TaskStatus.SUGGESTED)
    _make_task(db, title="Done task", status=TaskStatus.RESOLVED)
    db.commit()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        result = _run_tool(ListTasksTool())

    titles = sorted(t["title"] for t in result["tasks"])
    assert titles == ["Open task A", "Open task B"]
    assert result["count"] == 2


def test_list_tasks_resolved_filter(db):
    _make_task(db, title="Open", status=TaskStatus.ACTIVE)
    _make_task(db, title="Closed", status=TaskStatus.RESOLVED)
    _make_task(db, title="Dismissed", status=TaskStatus.DISMISSED)
    db.commit()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        result = _run_tool(ListTasksTool(), status="resolved")

    titles = sorted(t["title"] for t in result["tasks"])
    assert titles == ["Closed", "Dismissed"]


def test_list_tasks_all_status(db):
    _make_task(db, title="Open", status=TaskStatus.ACTIVE)
    _make_task(db, title="Closed", status=TaskStatus.RESOLVED)
    db.commit()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        result = _run_tool(ListTasksTool(), status="all")

    assert result["count"] == 2


def test_list_tasks_invalid_status_returns_error(db):
    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        result = _run_tool(ListTasksTool(), status="bogus")
    assert result.get("status") == "error"


def test_list_tasks_property_filter(db):
    from db.models import Property

    prop_a = Property(id="prop-a", org_id=1, creator_id=1, address_line1="A")
    prop_b = Property(id="prop-b", org_id=1, creator_id=1, address_line1="B")
    db.add_all([prop_a, prop_b])
    db.flush()
    _make_task(db, title="A1", property_id="prop-a")
    _make_task(db, title="A2", property_id="prop-a")
    _make_task(db, title="B1", property_id="prop-b")
    db.commit()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        result = _run_tool(ListTasksTool(), property_id="prop-a")

    titles = sorted(t["title"] for t in result["tasks"])
    assert titles == ["A1", "A2"]


def test_list_tasks_query_matches_title_and_goal(db):
    _make_task(db, title="Garbage disposal repair", goal="Fix it")
    _make_task(db, title="Lawn care", goal="Mow weekly with garbage rules")
    _make_task(db, title="Roof inspection", goal="Annual check")
    db.commit()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        result = _run_tool(ListTasksTool(), query="garbage")

    titles = sorted(t["title"] for t in result["tasks"])
    assert titles == ["Garbage disposal repair", "Lawn care"]


def test_list_tasks_urgency_filter(db):
    _make_task(db, title="urgent", urgency=Urgency.HIGH)
    _make_task(db, title="chill", urgency=Urgency.LOW)
    db.commit()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        result = _run_tool(ListTasksTool(), urgency="high")

    assert [t["title"] for t in result["tasks"]] == ["urgent"]


def test_list_tasks_limit_caps_results(db):
    for i in range(5):
        _make_task(db, title=f"task {i}")
    db.commit()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        result = _run_tool(ListTasksTool(), limit=2)

    assert result["count"] == 2


def test_list_tasks_returns_empty_when_none_match(db):
    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        result = _run_tool(ListTasksTool(), query="xyzzy-no-match")

    assert result["tasks"] == []
    assert result["count"] == 0


def test_list_tasks_account_scoping(db):
    """Tasks created under a different creator_id must not appear in results."""
    from db.models import User

    other = db.get(User, 2)
    if other is None:
        other = User(id=2, org_id=1, external_id="test-user-2", email="other@example.com", active=True)
        db.add(other)
        db.flush()

    _make_task(db, title="my task", creator_id=1)
    _make_task(db, title="other-account task", creator_id=2)
    db.commit()

    # Default fixture sets account_id=1; we should only see "my task".
    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        result = _run_tool(ListTasksTool())

    titles = [t["title"] for t in result["tasks"]]
    assert titles == ["my task"]


def test_list_tasks_serializes_enums_as_names(db):
    _make_task(db, title="x", status=TaskStatus.ACTIVE, urgency=Urgency.HIGH)
    db.commit()

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        result = _run_tool(ListTasksTool())

    assert result["tasks"][0]["status"] == "ACTIVE"
    assert result["tasks"][0]["urgency"] == "HIGH"
    assert result["tasks"][0]["mode"] == "MANUAL"
