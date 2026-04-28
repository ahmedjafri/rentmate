"""Tests for ``gql/services/extension_service.py`` — the helpers behind
the chrome-extension GraphQL surface (``searchTenants`` query and
``suggestReply`` mutation)."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backends.local_auth import reset_request_context, set_request_context
from db.models import Lease, Property, Tenant, Unit, User
from gql.services.extension_service import (
    _FALLBACK_REPLY,
    draft_reply,
    rank_tenants,
)


@contextmanager
def _request_scope(*, account_id=1, org_id=1):
    token = set_request_context(account_id=account_id, org_id=org_id)
    try:
        yield
    finally:
        reset_request_context(token)


def _seed_tenant(db, *, first, last, email=None, phone=None, with_lease=False):
    user = User(
        org_id=1, creator_id=1, user_type="tenant",
        first_name=first, last_name=last,
        email=email, phone=phone,
    )
    db.add(user)
    db.flush()
    tenant = Tenant(org_id=1, creator_id=1, user_id=user.id)
    db.add(tenant)
    db.flush()
    if with_lease:
        prop = Property(
            id=f"prop-{first.lower()}",
            org_id=1, creator_id=1,
            name="The Meadows", address_line1="1 Main St",
            property_type="multi_family",
        )
        unit = Unit(
            id=f"unit-{first.lower()}",
            org_id=1, creator_id=1,
            property_id=prop.id, label="1A",
        )
        db.add_all([prop, unit])
        db.flush()
        lease = Lease(
            id=f"lease-{first.lower()}",
            org_id=1, creator_id=1,
            tenant_id=tenant.id, property_id=prop.id, unit_id=unit.id,
            start_date=date(2024, 1, 1), end_date=date(2099, 1, 1),
            rent_amount=1500.0,
        )
        db.add(lease)
        db.flush()
    return tenant


# ─── rank_tenants ───────────────────────────────────────────────────────


def test_rank_tenants_exact_email_beats_partial_name(db):
    with _request_scope():
        _seed_tenant(db, first="Marcus", last="Johnson", email="marcus@example.com")
        _seed_tenant(db, first="Marcus", last="Williams", email="marcusw@example.com")

        out = rank_tenants(db, "marcus@example.com")

        # The first hit is the exact-email match (score 100). The second
        # tenant scores lower because "marcus@example.com" is a substring
        # of his email but not an exact match.
        assert len(out) >= 1
        assert out[0]["name"] == "Marcus Johnson"
        assert out[0]["score"] == 100
        if len(out) > 1:
            assert out[1]["score"] < 100


def test_rank_tenants_drops_zero_score_results(db):
    with _request_scope():
        _seed_tenant(db, first="Marcus", last="Johnson")
        _seed_tenant(db, first="Priya", last="Patel")

        assert rank_tenants(db, "xyzzy") == []


def test_rank_tenants_caps_at_three(db):
    with _request_scope():
        for i in range(5):
            _seed_tenant(db, first="Marcus", last=f"Last{i}")

        out = rank_tenants(db, "marcus")

        assert len(out) == 3
        assert all("Marcus" in r["name"] for r in out)


def test_rank_tenants_includes_property_and_unit_when_lease_exists(db):
    with _request_scope():
        _seed_tenant(db, first="Marcus", last="Johnson", with_lease=True)

        out = rank_tenants(db, "marcus")

        assert len(out) == 1
        assert out[0]["unit_label"] == "1A"
        assert out[0]["property_id"] == "prop-marcus"


def test_rank_tenants_blank_query_returns_empty(db):
    with _request_scope():
        _seed_tenant(db, first="Marcus", last="Johnson")
        assert rank_tenants(db, "   ") == []


# ─── draft_reply ────────────────────────────────────────────────────────


def _fake_completion(text: str):
    fake = MagicMock()
    fake.choices = [MagicMock(message=MagicMock(content=text))]
    return fake


def test_draft_reply_includes_tenant_name_in_system_prompt(db):
    with _request_scope():
        tenant = _seed_tenant(
            db, first="Marcus", last="Johnson", email="marcus@example.com",
            with_lease=True,
        )
        captured: dict = {}

        async def fake_acompletion(messages, **_kwargs):
            captured["system"] = messages[0]["content"]
            captured["user"] = messages[1]["content"]
            return _fake_completion("Hi Marcus — I'll get someone out to take a look.")

        with patch("litellm.acompletion", side_effect=fake_acompletion):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[
                    {"sender": "Marcus", "text": "The dishwasher leaked again."},
                ],
                header_title="Dishwasher leak",
                header_description=None,
                tenant_id=str(tenant.external_id),
                property_id=None,
            ))

    assert "Marcus Johnson" in captured["system"]
    assert "Dishwasher leak" in captured["system"]
    assert result["matched_tenant"]["tenant_id"] == str(tenant.external_id)
    assert result["suggestion"].startswith("Hi Marcus")


def test_draft_reply_falls_back_on_llm_error(db):
    with _request_scope():
        with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=RuntimeError("connection refused")):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "Tenant", "text": "Hi?"}],
                header_title=None,
                header_description=None,
                tenant_id=None,
                property_id=None,
            ))

    assert result["suggestion"] == _FALLBACK_REPLY
    assert result["matched_tenant"] is None
    assert result["fallback"] is True
    # Connection-style failures get the "endpoint unreachable" message,
    # not a generic LLM error blob.
    assert result["error"] is not None
    assert "unreachable" in result["error"].lower() or "base url" in result["error"].lower()


def test_draft_reply_classifies_auth_error(db):
    """The exact error PMs hit when the OpenAI/OpenRouter key is wrong
    deserves an actionable message that names the env var."""
    class FakeAuthError(Exception):
        pass
    FakeAuthError.__name__ = "AuthenticationError"

    with _request_scope():
        with patch(
            "litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=FakeAuthError("Incorrect API key provided: sk-xxx"),
        ):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id=None, property_id=None,
            ))

    assert result["fallback"] is True
    assert "LLM_API_KEY" in result["error"]


def test_draft_reply_success_has_no_error(db):
    """Happy path doesn't set ``error`` or ``fallback`` so the extension
    can trust the suggestion."""
    with _request_scope():
        async def fake_acompletion(*_args, **_kwargs):
            fake = MagicMock()
            fake.choices = [MagicMock(message=MagicMock(content="A real reply."))]
            return fake

        with patch("litellm.acompletion", side_effect=fake_acompletion):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id=None, property_id=None,
            ))

    assert result["suggestion"] == "A real reply."
    assert result["fallback"] is False
    assert result["error"] is None


def test_draft_reply_clamps_long_completion(db):
    with _request_scope():
        long_text = "x" * 1000

        async def fake_acompletion(*_args, **_kwargs):
            return _fake_completion(long_text)

        with patch("litellm.acompletion", side_effect=fake_acompletion):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id=None, property_id=None,
            ))

    assert len(result["suggestion"]) <= 500


def test_draft_reply_unknown_tenant_id_returns_no_match(db):
    with _request_scope():
        async def fake_acompletion(*_args, **_kwargs):
            return _fake_completion("OK.")

        with patch("litellm.acompletion", side_effect=fake_acompletion):
            result = asyncio.run(draft_reply(
                db,
                conversation_history=[{"sender": "T", "text": "hi"}],
                header_title=None, header_description=None,
                tenant_id="00000000-0000-0000-0000-000000000bad",
                property_id=None,
            ))

    assert result["matched_tenant"] is None
