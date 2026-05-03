"""Tests for ``AddTaskNoteTool`` — thin replacement for the old
``save_memory(scope='task')`` path."""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from unittest.mock import patch

from agent.tools import AddTaskNoteTool
from db.enums import TaskMode, TaskStatus
from db.models import Task
from services.number_allocator import NumberAllocator


@contextmanager
def _bind_session(db):
    with patch("rentmate.app.SessionLocal.session_factory", return_value=db), \
         patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None):
        yield


def _run(tool, **kwargs):
    return json.loads(asyncio.run(tool.execute(**kwargs)))


def _seed_task(db, *, title="Fix gutters"):
    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=1),
        org_id=1, creator_id=1,
        title=title, task_status=TaskStatus.ACTIVE, task_mode=TaskMode.MANUAL,
    )
    db.add(task)
    db.flush()
    return task


def test_appends_dated_note_to_task_context(db):
    task = _seed_task(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            AddTaskNoteTool(),
            task_id=str(task.id),
            note="Vendor confirmed Tuesday 2pm appointment.",
        )
    assert result["status"] == "ok", result
    db.refresh(task)
    assert "Vendor confirmed" in task.context
    # Dated stamp at the start.
    assert task.context.startswith("[")


def test_pii_stripped_from_note_before_persisting(db):
    task = _seed_task(db)
    db.commit()
    with _bind_session(db):
        result = _run(
            AddTaskNoteTool(),
            task_id=str(task.id),
            note="Reach Bob at +15555551234 or bob@plumber.com about Tuesday 2pm.",
        )
    assert result["status"] == "ok"
    db.refresh(task)
    assert "555-1234" not in task.context
    assert "555 5551234" not in task.context
    assert "@plumber.com" not in task.context
    assert "Tuesday 2pm" in task.context


def test_placeholder_task_id_rejected(db):
    db.commit()
    with _bind_session(db):
        result = _run(
            AddTaskNoteTool(),
            task_id="task_id_from_context",
            note="Some progress note.",
        )
    assert result["status"] == "error"
    assert "placeholder" in result["message"].lower()


def test_unknown_task_returns_error(db):
    db.commit()
    with _bind_session(db):
        result = _run(
            AddTaskNoteTool(),
            task_id="9999",
            note="Some progress note.",
        )
    assert result["status"] == "error"
    assert "not found" in result["message"]


def test_long_note_truncated_to_500_chars(db):
    task = _seed_task(db)
    db.commit()
    huge = "ok " * 500
    with _bind_session(db):
        result = _run(
            AddTaskNoteTool(),
            task_id=str(task.id),
            note=huge,
        )
    assert result["status"] == "ok"
    # The applied summary must respect the cap (with truncation marker).
    applied = result["applied_summary"]
    assert len(applied) <= 501  # 500 + ellipsis char
