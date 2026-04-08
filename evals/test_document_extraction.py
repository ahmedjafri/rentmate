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
