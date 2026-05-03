"""NLP pre-pass over a retrieval query — match real tenant / vendor /
property names so the heuristic ranker can boost items linked to them.

Cheap pure-Python — runs every retrieval, no LLM call. We strip
possessives ("priyas" → "priya"), drop a small object/stoplist
("house", "unit", "for"), then look up each remaining token (and
contiguous bigrams) against the live tenant / vendor / property
tables.

The output is fed back into ``RetrievalRequest`` via
``dataclasses.replace`` so the existing equality-based heuristic
boost in ``_heuristic_score`` (`+4.5` for a `tenant_id` match,
`+4.0` for a `property_id` match, etc.) fires for free.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from db.models import Lease, LeaseTenant, Property, Tenant, User

# Words that should never anchor entity extraction. Mostly stop-words
# plus location/object nouns ("house", "apt") that tell us *what kind*
# of place is being discussed, not *which one*.
_STOPWORDS: frozenset[str] = frozenset({
    # articles / prepositions / pronouns / generic verbs
    "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "at",
    "by", "is", "are", "be", "we", "i", "you", "they", "he", "she", "it",
    "my", "our", "your", "their", "his", "her", "this", "that", "these",
    "those", "need", "want", "would", "could", "please", "just", "send",
    "make", "do", "with", "from", "schedule", "about", "regarding", "re",
    # location object words — describe the shape, not the identity
    "house", "home", "homes", "apt", "apartment", "apartments", "unit",
    "units", "suite", "room", "property", "properties", "place", "places",
    "building", "buildings", "address", "site",
})

_POSSESSIVE_RE = re.compile(r"(\w+?)['']s\b", re.IGNORECASE)
_BARE_POSSESSIVE_RE = re.compile(r"(\w+?)s\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass
class QueryEntities:
    """Entities recognized in the query, ready to be merged into a RetrievalRequest."""
    tenant_ids: set[str] = field(default_factory=set)        # Tenant.external_id
    vendor_ids: set[str] = field(default_factory=set)        # User.external_id
    property_ids: set[str] = field(default_factory=set)      # Property.id
    unit_ids: set[str] = field(default_factory=set)          # Unit.id
    matched_names: list[str] = field(default_factory=list)   # for trace reasons


def _normalize_query(query: str) -> str:
    """Lowercase + strip possessives so 'Priya's' → 'priya'."""
    q = (query or "").lower()
    q = _POSSESSIVE_RE.sub(r"\1", q)
    return q


def _candidate_terms(query: str) -> tuple[set[str], set[str]]:
    """Return (single tokens, bigrams + trigrams) for matching.

    Bigrams/trigrams catch property names like "the meadows" or
    "harbor view" that no single token would resolve.

    Bare possessives ("priyas") generate both the original and the
    stripped form so we still match a candidate name "priya" even
    when the user didn't type an apostrophe.
    """
    raw_tokens = _TOKEN_RE.findall(_normalize_query(query))
    significant = [t for t in raw_tokens if len(t) > 1 and t not in _STOPWORDS]
    singles = set(significant)
    # Add stripped-trailing-s variants for tokens that look like a bare
    # possessive (>=4 chars ending in 's'). Cheap; collisions don't
    # matter because we only use these for membership tests.
    for token in list(singles):
        if len(token) >= 4 and token.endswith("s"):
            singles.add(token[:-1])
    # Bigrams + trigrams use the raw token stream so prepositions inside
    # multi-word names ("the meadows") still glue the phrase together.
    multis: set[str] = set()
    for n in (2, 3):
        for i in range(len(raw_tokens) - n + 1):
            window = raw_tokens[i : i + n]
            multis.add(" ".join(window))
    return singles, multis


def extract_query_entities(db: Session, query: str, *, org_id: int | None = None) -> QueryEntities:
    """Return entities mentioned in ``query`` that exist in this org's data.

    No-op (empty result) when nothing matches — most queries are abstract.
    """
    out = QueryEntities()
    if not query or not query.strip():
        return out

    singles, multis = _candidate_terms(query)
    if not singles and not multis:
        return out

    tenant_rows = _load_tenants(db, org_id=org_id)
    vendor_rows = _load_vendors(db, org_id=org_id)
    property_rows = _load_properties(db, org_id=org_id)

    today = date.today()

    # ── tenants ────────────────────────────────────────────────────────────
    # Drop ambiguous last-name-only matches (two tenants share the surname).
    last_name_counts: dict[str, int] = {}
    for row in tenant_rows:
        last_name_counts[row["last"]] = last_name_counts.get(row["last"], 0) + 1

    tenants_matched: list[dict] = []
    for row in tenant_rows:
        if row["full"] and row["full"] in multis:
            tenants_matched.append(row)
            continue
        if row["first"] and row["first"] in singles:
            tenants_matched.append(row)
            continue
        if row["last"] and row["last"] in singles and last_name_counts[row["last"]] == 1:
            tenants_matched.append(row)

    for row in tenants_matched:
        out.tenant_ids.add(row["external_id"])
        out.matched_names.append(row["display"])
        # Pull the tenant's *current* unit/property if they have an active lease.
        active_lease = _active_lease_for_tenant(db, row["pk"], today=today, org_id=row["org_id"])
        if active_lease is not None:
            if active_lease.unit_id:
                out.unit_ids.add(str(active_lease.unit_id))
            if active_lease.property_id:
                out.property_ids.add(str(active_lease.property_id))

    # ── vendors ────────────────────────────────────────────────────────────
    last_name_counts_v: dict[str, int] = {}
    for row in vendor_rows:
        last_name_counts_v[row["last"]] = last_name_counts_v.get(row["last"], 0) + 1
    for row in vendor_rows:
        if row["full"] and row["full"] in multis:
            out.vendor_ids.add(row["external_id"])
            out.matched_names.append(row["display"])
            continue
        if row["first"] and row["first"] in singles:
            out.vendor_ids.add(row["external_id"])
            out.matched_names.append(row["display"])
            continue
        if row["last"] and row["last"] in singles and last_name_counts_v[row["last"]] == 1:
            out.vendor_ids.add(row["external_id"])
            out.matched_names.append(row["display"])

    # ── properties ─────────────────────────────────────────────────────────
    for row in property_rows:
        # Match against the address line, the optional nickname, OR
        # any salient bigram/trigram from the address.
        addr_tokens = _TOKEN_RE.findall((row["address"] or "").lower())
        addr_bigrams = {
            " ".join(addr_tokens[i : i + 2])
            for i in range(len(addr_tokens) - 1)
        }
        if row["nickname"] and row["nickname"] in multis:
            out.property_ids.add(row["id"])
            out.matched_names.append(row["display"])
            continue
        if addr_bigrams & multis:
            out.property_ids.add(row["id"])
            out.matched_names.append(row["display"])
            continue
        # Fallback: a unique address token (skip generic ones like "ave", "st")
        for token in addr_tokens:
            if (
                token not in _STOPWORDS
                and len(token) > 3
                and token not in {"road", "lane", "drive", "street", "avenue"}
                and token in singles
            ):
                out.property_ids.add(row["id"])
                out.matched_names.append(row["display"])
                break

    return out


# ─── DB lookup helpers ─────────────────────────────────────────────────────


def _load_tenants(db: Session, *, org_id: int | None) -> list[dict]:
    rows = []
    for tenant, user in (
        db.query(Tenant, User)
        .join(User, (User.org_id == Tenant.org_id) & (User.id == Tenant.user_id))
        .filter(*( [Tenant.org_id == org_id] if org_id is not None else []))
        .all()
    ):
        first = (user.first_name or "").lower()
        last = (user.last_name or "").lower()
        full = " ".join(filter(None, [first, last]))
        display = " ".join(filter(None, [user.first_name, user.last_name])) or "Tenant"
        rows.append({
            "pk": tenant.id,
            "org_id": tenant.org_id,
            "external_id": str(tenant.external_id),
            "first": first,
            "last": last,
            "full": full,
            "display": display,
        })
    return rows


def _load_vendors(db: Session, *, org_id: int | None) -> list[dict]:
    rows = []
    for user in (
        db.query(User)
        .filter(User.user_type == "vendor")
        .filter(*( [User.org_id == org_id] if org_id is not None else []))
        .all()
    ):
        first = (user.first_name or "").lower()
        last = (user.last_name or "").lower()
        full = " ".join(filter(None, [first, last]))
        display = " ".join(filter(None, [user.first_name, user.last_name])) or "Vendor"
        rows.append({
            "external_id": str(user.external_id),
            "first": first,
            "last": last,
            "full": full,
            "display": display,
        })
    return rows


def _load_properties(db: Session, *, org_id: int | None) -> list[dict]:
    rows = []
    for prop in (
        db.query(Property)
        .filter(*( [Property.org_id == org_id] if org_id is not None else []))
        .all()
    ):
        nickname = (prop.name or "").lower() or None
        address = prop.address_line1 or ""
        display = prop.name or address or f"Property {prop.id[:8]}"
        rows.append({
            "id": str(prop.id),
            "nickname": nickname,
            "address": address,
            "display": display,
        })
    return rows


def _active_lease_for_tenant(db: Session, tenant_id: int, *, today: date, org_id: int) -> Lease | None:
    """Return the tenant's currently-active lease, or ``None``.

    Deliberately does NOT fall back to expired/historical leases — when
    the tenant has no current lease we still record the tenant_id but
    leave unit_id / property_id alone so we don't mis-anchor a query
    on a place they no longer live.
    """
    leases = (
        db.query(Lease)
        .join(LeaseTenant)
        .filter(LeaseTenant.tenant_id == tenant_id, Lease.org_id == org_id)
        .all()
    )
    active = [l for l in leases if l.end_date >= today]
    if not active:
        return None
    active.sort(key=lambda l: l.end_date, reverse=True)
    return active[0]
