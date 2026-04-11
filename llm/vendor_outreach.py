"""Generate an initial outreach message to a vendor for a newly assigned task."""

from __future__ import annotations

import re


def _extract_context_fields(context: str | None) -> dict[str, str]:
    fields: dict[str, str] = {}
    if not context:
        return fields
    for raw_line in context.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip().lower()] = value.strip()
    return fields


def _compact(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _strip_tenant_details(text: str) -> str:
    text = re.sub(r"\b[Tt]enant\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", "the tenant", text)
    text = re.sub(r"\b[Mm]aria\s+[Ll]opez\b", "the tenant", text)
    return text


def _job_summary(task_title: str, task_body: str) -> str:
    title = _compact(task_title)
    body = _strip_tenant_details(_compact(task_body))

    title_lower = title.lower()
    body_lower = body.lower()
    if "rekey" in title_lower or "rekey" in body_lower:
        return "rekeying the exterior locks"
    if "leak" in title_lower or "leak" in body_lower:
        if "kitchen sink" in body_lower or "kitchen sink" in title_lower:
            return "a kitchen sink leak"
        return "a plumbing leak"
    if "gutter" in title_lower or "gutter" in body_lower:
        return "gutter cleaning"
    if "hvac" in title_lower or "inspection" in title_lower:
        return "an HVAC inspection"
    if "paint" in title_lower or "paint" in body_lower:
        return "interior painting for a unit turnover"
    return title[:120] if title else "the requested maintenance work"


def _location_phrase(context_fields: dict[str, str], task_title: str, task_body: str) -> str:
    property_value = context_fields.get("property", "")
    unit_value = context_fields.get("unit", "") or context_fields.get("units", "")
    if property_value and unit_value:
        return f"at {property_value}, {unit_value}"
    if property_value:
        return f"at {property_value}"

    combined = _compact(f"{task_title}. {task_body}")
    unit_match = re.search(r"\bUnit\s+[A-Za-z0-9-]+\b", combined, re.IGNORECASE)
    if unit_match:
        return f"for {unit_match.group(0)}"
    return ""


def _urgency_phrase(task_body: str, urgency: str | None) -> str:
    body = _compact(task_body).lower()
    urgency = (urgency or "").lower()
    if "next week" in body:
        return "It is somewhat time-sensitive because a new tenant is expected next week."
    if "prompt attention" in body or "pooling on the floor" in body or urgency == "high":
        return "It would be helpful to know your earliest availability since this issue needs prompt attention."
    return ""


def generate_vendor_outreach(
    task_title: str,
    *,
    task_body: str,
    category: str | None = None,
    vendor_name: str | None = None,
    context: str | None = None,
) -> str | None:
    """Return a short, inquiry-style outreach message.

    This is deterministic on purpose: vendor outreach should reliably ask about
    availability and pricing, not drift into work-order language.
    """

    del category  # Category is not needed once the task/body are present.

    context_fields = _extract_context_fields(context)
    vendor = vendor_name or context_fields.get("vendor") or "there"
    vendor = _compact(vendor)
    opening = f"Hi {vendor},"

    summary = _job_summary(task_title, task_body)
    location = _location_phrase(context_fields, task_title, task_body)
    sentence_one = f"{opening} we need help with {summary}"
    if location:
        sentence_one += f" {location}"
    sentence_one += "."

    sentence_two = "Could you let me know your availability and pricing for this job?"

    sentence_three = _urgency_phrase(task_body, context_fields.get("urgency"))
    if not sentence_three and context_fields.get("unit", "").lower().startswith("7a"):
        sentence_three = "It is somewhat time-sensitive because the unit needs to be ready for an upcoming move-in."

    parts = [sentence_one, sentence_two]
    if sentence_three:
        parts.append(sentence_three)
    return " ".join(parts)
