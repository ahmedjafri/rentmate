"""
Generate a suggested draft action for a newly created waiting_approval task.
Returns the suggested message text, or None if generation fails or is unavailable.
"""
import logging
import os

from agent.litellm_utils import completion_with_retries
from db.enums import TaskCategory

logger = logging.getLogger("rentmate.suggest")


_SYSTEM = (
    "You are RentMate, a property management AI assistant. "
    "When given the details of a pending task, draft a short, professional message "
    "that the property manager could send to the relevant party (tenant, vendor, or contractor) "
    "to address it. Be concise — 2–4 sentences. Do not include a subject line. "
    "Do not add placeholders like [Name] — use the actual names if provided, "
    "or write generically if not. Do not explain what you are doing; just write the message.\n\n"
    "PRIVACY: When drafting messages to vendors or contractors, never include tenant "
    "personal information such as email addresses, phone numbers, or full names. "
    "Refer to tenants generically as 'the tenant'. Tell vendors to coordinate "
    "access through the property manager."
)

_CATEGORY_HINTS = {
    TaskCategory.MAINTENANCE: "This is a maintenance task. Draft a message to a vendor or contractor to schedule the work.",
    TaskCategory.RENT: "This is a rent/payment task. Draft a polite but firm message to the tenant about the outstanding payment.",
    TaskCategory.LEASING: "This is a leasing task. Draft a message to the tenant about lease renewal or move-out logistics.",
    TaskCategory.COMPLIANCE: "This is a compliance task. Draft a message to the relevant party (tenant or internally) requesting the needed action.",
}


def generate_task_suggestion(
    subject: str,
    *, context_body: str,
    category: str,
    tenant_name: str | None = None,
    property_address: str | None = None,
) -> str | None:
    """Call the LLM to generate a suggested draft message for the task.
    Returns None on any error so task creation always succeeds."""
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return None
    try:
        category_hint = _CATEGORY_HINTS.get(category, "")
        parts = [f"Task: {subject}", f"Context: {context_body}"]
        if tenant_name:
            parts.append(f"Tenant: {tenant_name}")
        if property_address:
            parts.append(f"Property: {property_address}")
        if category_hint:
            parts.append(category_hint)
        response, _, _ = completion_with_retries(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": "\n".join(parts)},
            ],
            model=os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
            api_base=os.getenv("LLM_BASE_URL") or None,
            max_tokens=200,
            temperature=0.4,
        )
        text = response.choices[0].message.content or ""
        return text.strip() or None
    except Exception as exc:
        logger.warning("suggest: LLM call failed (%s) — skipping draft", exc)
        return None
