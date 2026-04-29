"""Integration tests for the chrome-extension GraphQL surface.

Exercises ``Query.searchTenants`` and ``Mutation.suggestReply`` end-to-end
through the strawberry schema, including auth gating.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from backends.local_auth import reset_request_context, set_request_context
from db.models import Lease, Property, Tenant, Unit, User
from gql.schema import schema


def _execute(*args, **kwargs):
    return asyncio.run(schema.execute(*args, **kwargs))


@contextmanager
def _request_scope(*, account_id=1, org_id=1):
    token = set_request_context(account_id=account_id, org_id=org_id)
    try:
        yield
    finally:
        reset_request_context(token)


def _ctx(db, *, authed=True):
    return {
        "db_session": db,
        "user": (
            {"id": 1, "uid": "user-external-123", "email": "admin@example.com"}
            if authed else None
        ),
    }


def _seed_tenant(db, *, first, last, email=None, with_lease=False):
    user = User(
        org_id=1, creator_id=1, user_type="tenant",
        first_name=first, last_name=last, email=email,
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


def test_search_tenants_requires_auth(db):
    with _request_scope():
        result = _execute(
            """
            query {
              searchTenants(query: "marcus") {
                tenantId
                name
                score
              }
            }
            """,
            context_value=_ctx(db, authed=False),
        )
    assert result.errors
    assert "Not authenticated" in result.errors[0].message


def test_search_tenants_returns_ranked_payload(db):
    with _request_scope():
        _seed_tenant(db, first="Marcus", last="Johnson", with_lease=True)
        _seed_tenant(db, first="Priya", last="Patel")

        result = _execute(
            """
            query {
              searchTenants(query: "marcus") {
                tenantId
                name
                score
                unitLabel
                propertyId
              }
            }
            """,
            context_value=_ctx(db),
        )
    assert result.errors is None, result.errors
    rows = result.data["searchTenants"]
    assert len(rows) == 1
    assert rows[0]["name"] == "Marcus Johnson"
    assert rows[0]["score"] >= 25
    assert rows[0]["unitLabel"] == "1A"


def test_suggest_reply_requires_auth(db):
    with _request_scope():
        result = _execute(
            """
            mutation {
              suggestReply(input: {
                conversationHistory: [{sender: "Tenant", text: "hi"}]
              }) {
                suggestion
              }
            }
            """,
            context_value=_ctx(db, authed=False),
        )
    assert result.errors
    assert "Not authenticated" in result.errors[0].message


def test_suggest_reply_returns_matched_tenant_in_payload(db):
    with _request_scope():
        tenant = _seed_tenant(
            db, first="Marcus", last="Johnson", email="marcus@example.com",
            with_lease=True,
        )

        async def fake_acompletion(*_args, **_kwargs):
            fake = MagicMock()
            fake.choices = [MagicMock(message=MagicMock(
                content="Hi Marcus — got it, will follow up shortly.",
            ))]
            return fake

        with patch("litellm.acompletion", side_effect=fake_acompletion):
            result = _execute(
                """
                mutation Suggest($input: SuggestReplyInput!) {
                  suggestReply(input: $input) {
                    suggestion
                    matchedTenant {
                      tenantId
                      name
                      score
                    }
                  }
                }
                """,
                variable_values={
                    "input": {
                        "conversationHistory": [
                            {"sender": "Marcus", "text": "Dishwasher leaking again."},
                        ],
                        "headerTitle": "Dishwasher leak",
                        "tenantId": str(tenant.external_id),
                    },
                },
                context_value=_ctx(db),
            )
    assert result.errors is None, result.errors
    payload = result.data["suggestReply"]
    assert "Marcus" in payload["suggestion"]
    assert payload["matchedTenant"]["tenantId"] == str(tenant.external_id)
    assert payload["matchedTenant"]["name"] == "Marcus Johnson"
    assert payload["matchedTenant"]["score"] == 100


def test_suggest_reply_returns_canned_fallback_on_llm_error(db):
    with _request_scope():
        async def boom(*_args, **_kwargs):
            raise RuntimeError("network down")

        with patch("litellm.acompletion", side_effect=boom):
            result = _execute(
                """
                mutation Suggest($input: SuggestReplyInput!) {
                  suggestReply(input: $input) {
                    suggestion
                    matchedTenant { tenantId }
                  }
                }
                """,
                variable_values={
                    "input": {
                        "conversationHistory": [{"sender": "T", "text": "hi"}],
                    },
                },
                context_value=_ctx(db),
            )
    assert result.errors is None, result.errors
    assert result.data["suggestReply"]["matchedTenant"] is None
    assert result.data["suggestReply"]["suggestion"] == "I'll follow up on this shortly."
