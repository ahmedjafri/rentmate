"""Regression tests for the request-context bug in
``handlers.chat._agent_task_autoreply_inner``.

Background callers (reply_scanner loop, portal autoreply daemon
thread, demo simulator) invoke ``agent_task_autoreply`` without
having gone through HTTP auth. Before the fix, the first downstream
call to ``resolve_account_id`` blew up with
``RuntimeError: No account context set …``.
"""
from __future__ import annotations

from unittest.mock import patch

from db.enums import TaskStatus
from db.models import Conversation, ConversationType, Task
from handlers.chat import _agent_task_autoreply_inner
from integrations.local_auth import reset_request_context, resolve_account_id, resolve_org_id, set_request_context
from services.number_allocator import NumberAllocator


def _seed_task(db, *, creator_id=1, org_id=1):
    convo = Conversation(
        org_id=org_id, creator_id=creator_id, subject="Auto",
        conversation_type=ConversationType.TASK_AI,
        is_group=False, is_archived=False,
    )
    db.add(convo)
    db.flush()
    task = Task(
        id=NumberAllocator.allocate_next(db, entity_type="task", org_id=org_id),
        org_id=org_id, creator_id=creator_id,
        title="Test", task_status=TaskStatus.ACTIVE,
        ai_conversation_id=convo.id,
    )
    db.add(task)
    db.flush()
    return task


def test_autoreply_inner_sets_request_context_from_task(db):
    """Without prior HTTP auth, the inner autoreply must derive
    creator_id + org_id from the task and seed the request context
    so the next ``resolve_account_id`` call succeeds."""
    task = _seed_task(db, creator_id=1, org_id=1)
    db.commit()

    captured = {}

    def _fake_compute_hash(_t):
        # Prove the request context is live by the time we get past
        # the task lookup. ``resolve_account_id`` would raise without
        # the fix.
        captured["account_id"] = resolve_account_id()
        captured["org_id"] = resolve_org_id()
        return "stub-hash"

    with patch("db.session.SessionLocal.session_factory", return_value=db), \
         patch.object(db, "close", lambda: None), \
         patch("handlers.chat._compute_autoreply_hash", side_effect=_fake_compute_hash), \
         patch("handlers.chat._autoreply_state", {str(task.id): "stub-hash"}):
        # Same hash → returns None early (before kicking off the agent).
        # That's fine — we only care that the context was set in
        # ``_compute_autoreply_hash``.
        result = _agent_task_autoreply_inner(str(task.id))

    assert result is None
    assert captured == {"account_id": 1, "org_id": 1}


def test_autoreply_inner_resets_request_context_on_exit(db):
    """After the inner autoreply returns, the caller's prior context
    (or empty) must be restored. Otherwise per-task context would
    leak across reply_scanner loop iterations."""
    task = _seed_task(db, creator_id=1, org_id=1)
    db.commit()

    # Pre-seed a different context so we can verify it's restored.
    pre_token = set_request_context(account_id=999, org_id=999)
    try:
        with patch("db.session.SessionLocal.session_factory", return_value=db), \
             patch.object(db, "close", lambda: None), \
             patch("handlers.chat._compute_autoreply_hash", return_value="stub-hash"), \
             patch("handlers.chat._autoreply_state", {str(task.id): "stub-hash"}):
            _agent_task_autoreply_inner(str(task.id))
        assert resolve_account_id() == 999
        assert resolve_org_id() == 999
    finally:
        reset_request_context(pre_token)
