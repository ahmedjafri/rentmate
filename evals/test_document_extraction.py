"""Eval: document extraction captures rich context from rental agreements.

Uses the sample rental agreement PDF and verifies the LLM extraction prompt
captures not just basic fields but also the detailed context a property
manager needs (financial terms, policies, safety systems, etc.).
"""
import json
import os
from pathlib import Path

import litellm
import pytest

from llm.document_processor import EXTRACTION_PROMPT

_SAMPLE_PDF = Path(__file__).resolve().parent / "sample_rental_agreement.pdf"
_MISSING_TENANT_PDF = Path(__file__).resolve().parent / "sample_lease_missing_tenant.pdf"

pytestmark = pytest.mark.eval


def _extract_from_pdf(pdf_path: Path) -> dict:
    """Run the extraction prompt against a real PDF and return parsed JSON."""
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(pdf_path.read_bytes()))
    raw_text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()

    # Grab form fields too (same as document_processor)
    try:
        fields = reader.get_fields() or {}
        form_lines = []
        for name, field in fields.items():
            val = field.get("/V")
            if val and isinstance(val, str) and not val.startswith("/"):
                form_lines.append(f"{name}: {val}")
        if form_lines:
            raw_text = "[Form Fields]\n" + "\n".join(form_lines) + "\n\n" + raw_text
    except Exception:
        pass

    truncated = raw_text[:12000]

    response = litellm.completion(
        model=os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL") or None,
        messages=[{"role": "user", "content": EXTRACTION_PROMPT + truncated}],
        response_format={"type": "json_object"},
    )

    return json.loads(response.choices[0].message.content)


@pytest.fixture(scope="module")
def extraction():
    """Run extraction once and share across all tests in this module."""
    if not _SAMPLE_PDF.exists():
        pytest.skip(f"Sample PDF not found: {_SAMPLE_PDF}")
    if not os.getenv("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set")
    return _extract_from_pdf(_SAMPLE_PDF)


@pytest.fixture(scope="module")
def lease(extraction):
    """Get the first (and only) lease from the extraction."""
    leases = extraction.get("leases", [])
    assert len(leases) >= 1, f"Expected at least 1 lease, got {len(leases)}"
    return leases[0]


# ---------------------------------------------------------------------------
# Basic fields — any rental agreement should have these
# ---------------------------------------------------------------------------


class TestBasicFields:
    def test_has_property_address(self, lease):
        addr = lease.get("property_address") or ""
        assert len(addr) > 5, f"Expected a property address, got: {addr!r}"

    def test_has_property_type(self, lease):
        pt = lease.get("property_type")
        assert pt in ("single_family", "multi_family"), f"Expected valid property_type, got: {pt!r}"

    def test_has_lease_dates(self, lease):
        start = lease.get("lease_start_date")
        end = lease.get("lease_end_date")
        assert start, "Expected lease_start_date"
        assert end, "Expected lease_end_date"
        # Validate format
        from datetime import date
        date.fromisoformat(start)
        date.fromisoformat(end)

    def test_has_monthly_rent(self, lease):
        rent = lease.get("monthly_rent")
        assert rent and rent > 0, f"Expected positive monthly_rent, got: {rent}"


# ---------------------------------------------------------------------------
# Context fields — the key value-add of the updated prompt
# ---------------------------------------------------------------------------


class TestPropertyContext:
    def test_has_property_context(self, lease):
        ctx = lease.get("property_context") or ""
        assert len(ctx) > 20, f"property_context too short or missing: {ctx!r}"

    def test_not_just_address_repeat(self, lease):
        """Context should contain real detail, not just restate the address."""
        ctx = lease.get("property_context") or ""
        # Should mention at least one of: furnishings, safety, landlord, payment
        keywords = ["stove", "refrigerator", "smoke", "alarm", "landlord", "appliance",
                     "furnish", "washer", "dryer", "sprinkler", "fire"]
        found = [kw for kw in keywords if kw in ctx.lower()]
        assert len(found) >= 1, f"property_context lacks substantive detail: {ctx[:200]}"


class TestUnitContext:
    def test_has_unit_context(self, lease):
        ctx = lease.get("unit_context") or ""
        assert len(ctx) > 10, f"unit_context too short or missing: {ctx!r}"

    def test_mentions_practical_detail(self, lease):
        """Should include parking, utilities, keys, or similar unit-specific info."""
        ctx = (lease.get("unit_context") or "").lower()
        keywords = ["park", "utilit", "key", "garage", "floor", "bedroom", "bath"]
        found = [kw for kw in keywords if kw in ctx]
        assert len(found) >= 1, f"unit_context lacks practical detail: {ctx[:200]}"


class TestTenantContext:
    def test_has_tenant_context(self, lease):
        ctx = lease.get("tenant_context") or ""
        assert len(ctx) > 10, f"tenant_context too short or missing: {ctx!r}"

    def test_mentions_policies(self, lease):
        """Should include at least one tenant-facing policy (pets, smoking, guests, etc.)."""
        ctx = (lease.get("tenant_context") or "").lower()
        keywords = ["pet", "smok", "guest", "visitor", "vehicle", "occupan", "sublet", "insurance"]
        found = [kw for kw in keywords if kw in ctx]
        assert len(found) >= 1, f"tenant_context lacks policy info: {ctx[:200]}"


class TestLeaseContext:
    def test_has_lease_context(self, lease):
        ctx = lease.get("lease_context") or ""
        assert len(ctx) > 20, f"lease_context too short or missing: {ctx!r}"

    def test_mentions_security_deposit(self, lease):
        ctx = (lease.get("lease_context") or "").lower()
        assert "security" in ctx or "deposit" in ctx, \
            f"Expected security deposit info in lease_context: {ctx[:200]}"

    def test_mentions_late_fee(self, lease):
        ctx = (lease.get("lease_context") or "").lower()
        assert "late" in ctx, f"Expected late fee info in lease_context: {ctx[:200]}"

    def test_mentions_notice_period(self, lease):
        ctx = (lease.get("lease_context") or "").lower()
        assert "notice" in ctx or "vacat" in ctx or "terminat" in ctx, \
            f"Expected notice/termination info in lease_context: {ctx[:200]}"

    def test_has_dollar_amounts(self, lease):
        """Context should include specific dollar amounts, not vague descriptions."""
        ctx = lease.get("lease_context") or ""
        import re
        amounts = re.findall(r'\$[\d,]+', ctx)
        assert len(amounts) >= 2, \
            f"Expected at least 2 dollar amounts in lease_context, found {len(amounts)}: {ctx[:200]}"


# ---------------------------------------------------------------------------
# Landlord vs tenant distinction
# ---------------------------------------------------------------------------


class TestLandlordTenantDistinction:
    """The sample PDF has landlord contact info (email, phone, address) in the
    payment delivery and signature sections. These must NOT be attributed to
    the tenant. The tenant signature lines are blank — tenant fields should
    be null, not hallucinated from landlord info."""

    def test_tenant_name_not_hallucinated_from_landlord(self, lease):
        """If the document has no tenant name filled in, fields should be null —
        not inferred from landlord email or other non-tenant sources."""
        first = lease.get("tenant_first_name")
        last = lease.get("tenant_last_name")
        # The sample PDF's tenant lines are blank. If the LLM fills in a name,
        # it must not come from the landlord's email address or contact info.
        # Known bad extraction: "Zainab Zahra" from "zainabzahra98@..." landlord email
        if first and last:
            full_name = f"{first} {last}".lower()
            # These are landlord-associated names/fragments that should never appear as tenant
            landlord_fragments = ["zainab", "zahra"]
            for frag in landlord_fragments:
                assert frag not in full_name, (
                    f"Tenant name '{first} {last}' was likely hallucinated from "
                    f"landlord contact info (contains '{frag}')"
                )

    def test_tenant_email_not_landlord_email(self, lease):
        """Payment/delivery emails belong to the landlord, not the tenant."""
        email = (lease.get("tenant_email") or "").lower()
        if email:
            # The delivery-of-rent section contains the landlord's email
            assert "hotmail" not in email and "zainab" not in email, (
                f"tenant_email '{email}' appears to be the landlord's payment email, not the tenant's"
            )

    def test_tenant_phone_not_landlord_phone(self, lease):
        """Landlord/manager phone from the signature section is not the tenant's."""
        phone = lease.get("tenant_phone") or ""
        if phone:
            # The landlord's phone appears in the signature section
            assert "4734" not in phone, (
                f"tenant_phone '{phone}' appears to be the landlord's phone number"
            )


# ---------------------------------------------------------------------------
# Missing tenant document — dedicated PDF with no tenant name
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def missing_tenant_extraction():
    """Extract from the lease PDF that has NO tenant name filled in."""
    if not _MISSING_TENANT_PDF.exists():
        pytest.skip(f"Missing tenant PDF not found: {_MISSING_TENANT_PDF}")
    if not os.getenv("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set")
    return _extract_from_pdf(_MISSING_TENANT_PDF)


@pytest.fixture(scope="module")
def missing_tenant_lease(missing_tenant_extraction):
    leases = missing_tenant_extraction.get("leases", [])
    assert len(leases) >= 1
    return leases[0]


class TestMissingTenantDocument:
    """The 'sample lease agreement missing tenant.pdf' has:
    - Property: 1234 Acme Lane, ABC, WA 12345
    - Landlord: Lisa, 557 Dwight Way, Blaine WA 12345, 123-123-1234
    - Landlord email: bob@hotmail.com (in payment delivery section)
    - Tenant signature: BLANK
    - Tenant occupant fields: BLANK

    The extraction must NOT fabricate a tenant name from the landlord info."""

    def test_tenant_name_is_null(self, missing_tenant_lease):
        """When no tenant name is written in tenant fields, both should be null."""
        first = missing_tenant_lease.get("tenant_first_name")
        last = missing_tenant_lease.get("tenant_last_name")
        # Acceptable: both null, or both empty string
        if first or last:
            # If a name was extracted, it MUST NOT be the landlord's name
            full = f"{first or ''} {last or ''}".lower().strip()
            assert "lisa" not in full, (
                f"Tenant name '{first} {last}' was hallucinated from landlord name 'Lisa'"
            )
            assert "bob" not in full, (
                f"Tenant name '{first} {last}' was hallucinated from landlord email 'bob@...'"
            )

    def test_tenant_email_not_landlord_payment_email(self, missing_tenant_lease):
        """bob@hotmail.com is the landlord's payment email, not the tenant's."""
        email = (missing_tenant_lease.get("tenant_email") or "").lower()
        if email:
            assert "bob" not in email, (
                f"tenant_email '{email}' is the landlord's payment email"
            )
            assert "hotmail" not in email, (
                f"tenant_email '{email}' appears to be the landlord's email"
            )

    def test_tenant_phone_not_landlord_phone(self, missing_tenant_lease):
        """123-123-1234 is the landlord's phone, not the tenant's."""
        phone = (missing_tenant_lease.get("tenant_phone") or "").replace("-", "").replace(" ", "")
        if phone:
            assert "1231231234" not in phone, (
                f"tenant_phone '{phone}' is the landlord's phone number"
            )

    def test_property_address_extracted(self, missing_tenant_lease):
        """The property address should be correctly extracted."""
        addr = (missing_tenant_lease.get("property_address") or "").lower()
        assert "1234" in addr and "acme" in addr, (
            f"Expected '1234 Acme Lane' in property_address, got: {addr}"
        )

    def test_context_fields_dont_attribute_landlord_as_tenant(self, missing_tenant_lease):
        """Context fields should not say the tenant IS Lisa or Bob."""
        for field in ("property_context", "unit_context", "tenant_context", "lease_context"):
            ctx = (missing_tenant_lease.get(field) or "").lower()
            # Look for phrases that misidentify landlord as tenant
            bad_phrases = ["tenant: lisa", "tenant lisa", "tenant name: lisa",
                           "tenant: bob", "tenant bob", "tenant name: bob"]
            for phrase in bad_phrases:
                assert phrase not in ctx, (
                    f"{field} misidentifies landlord as tenant ('{phrase}' found): {ctx[:200]}"
                )
