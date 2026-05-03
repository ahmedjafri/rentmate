"""Unit tests for the query entity extractor."""
import uuid
from datetime import date

from agent.query_entities import _candidate_terms, extract_query_entities
from db.models import Lease, Property, Tenant, Unit, User


def _add_user(db, *, first: str, last: str, user_type: str = "tenant", uid: int | None = None):
    user = User(
        id=uid,
        org_id=1,
        creator_id=1,
        user_type=user_type,
        first_name=first,
        last_name=last,
        active=True,
    )
    db.add(user)
    db.flush()
    return user


def _add_tenant(db, *, first: str, last: str):
    user = _add_user(db, first=first, last=last)
    tenant = Tenant(org_id=1, creator_id=1, user_id=user.id)
    db.add(tenant)
    db.flush()
    return tenant


def _add_property(db, *, address: str, name: str | None = None, prop_id: str | None = None) -> Property:
    prop = Property(
        id=prop_id or f"prop-{address.replace(' ', '-')}",
        org_id=1,
        creator_id=1,
        address_line1=address,
        name=name,
        property_type="multi_family",
        source="manual",
    )
    db.add(prop)
    db.flush()
    return prop


def _add_unit(db, prop: Property, label: str, unit_id: str | None = None) -> Unit:
    unit = Unit(
        id=unit_id or f"unit-{prop.id}-{label}",
        org_id=1,
        creator_id=1,
        property_id=prop.id,
        label=label,
    )
    db.add(unit)
    db.flush()
    return unit


def _add_lease(db, tenant: Tenant, unit: Unit, prop: Property, *, end: date) -> Lease:
    # Lease.id is ``String(36)``; the previous ``f"lease-{tenant.id}-{unit.id}"``
    # format collided with the cap when unit.id encoded a long property name
    # ("lease-105-unit-prop-500-Meadow-Way-1B" = 37 chars). UUID-based id
    # keeps the fixture stable regardless of property/unit naming.
    lease = Lease(
        id=str(uuid.uuid4()),
        org_id=1,
        creator_id=1,
        tenant_id=tenant.id,
        unit_id=unit.id,
        property_id=prop.id,
        start_date=date(2024, 1, 1),
        end_date=end,
        rent_amount=1500,
        payment_status="current",
    )
    db.add(lease)
    db.flush()
    return lease


def test_candidate_terms_strips_possessives_and_stopwords():
    singles, multis = _candidate_terms("We need to schedule a gutter cleaning for priya's house")
    assert "priya" in singles
    assert "gutter" in singles
    assert "cleaning" in singles
    # stopwords + object-noun "house" filtered from singles
    assert "for" not in singles
    assert "to" not in singles
    assert "house" not in singles
    # bigrams keep filler so multi-word property names ("the meadows") still resolve
    assert "the meadows" not in multis  # not in this query, but the form is supported
    assert "schedule a" in multis


def test_extract_matches_tenant_first_name_and_pulls_active_unit(db):
    tenant = _add_tenant(db, first="Priya", last="Patel")
    prop = _add_property(db, address="500 Meadow Way", name="The Meadows")
    unit = _add_unit(db, prop, "1B")
    _add_lease(db, tenant, unit, prop, end=date(2099, 12, 31))
    db.commit()

    extracted = extract_query_entities(db, "schedule gutter cleaning for priyas house", org_id=1)

    assert tenant.external_id in extracted.tenant_ids
    assert unit.id in extracted.unit_ids
    assert prop.id in extracted.property_ids
    assert "Priya Patel" in extracted.matched_names


def test_extract_matches_property_nickname_via_bigram(db):
    prop = _add_property(db, address="500 Meadow Way", name="The Meadows", prop_id="prop-meadows")
    db.commit()

    extracted = extract_query_entities(db, "rent statement for the meadows", org_id=1)

    assert prop.id in extracted.property_ids


def test_extract_drops_ambiguous_last_name_only_match(db):
    _add_tenant(db, first="Priya", last="Patel")
    _add_tenant(db, first="Vikram", last="Patel")
    db.commit()

    # "patel" alone is ambiguous → no tenant chosen.
    extracted = extract_query_entities(db, "follow up with patel", org_id=1)
    assert extracted.tenant_ids == set()


def test_extract_returns_empty_for_abstract_query(db):
    _add_tenant(db, first="Priya", last="Patel")
    db.commit()

    extracted = extract_query_entities(db, "find recent maintenance receipts", org_id=1)
    assert extracted.tenant_ids == set()
    assert extracted.property_ids == set()
    assert extracted.matched_names == []


def test_extract_handles_apostrophe_possessive(db):
    tenant = _add_tenant(db, first="Tyler", last="Brooks")
    db.commit()

    extracted = extract_query_entities(db, "Did Tyler's repair finish?", org_id=1)
    assert tenant.external_id in extracted.tenant_ids
