"""
Generate a suggested draft action for a newly created waiting_approval task.
Returns the suggested message text, or None if generation fails or is unavailable.
"""
import logging
import os

logger = logging.getLogger("rentmate.suggest")

_LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
_LLM_API_KEY = os.getenv("LLM_API_KEY")
_LLM_BASE_URL = os.getenv("LLM_BASE_URL") or None

_SYSTEM = (
    "You are RentMate, a property management AI assistant. "
    "When given the details of a pending task, draft a short, professional message "
    "that the property manager could send to the relevant party (tenant, vendor, or contractor) "
    "to address it. Be concise — 2–4 sentences. Do not include a subject line. "
    "Do not add placeholders like [Name] — use the actual names if provided, "
    "or write generically if not. Do not explain what you are doing; just write the message."
)

_CATEGORY_HINTS = {
    "maintenance": "This is a maintenance task. Draft a message to a vendor or contractor to schedule the work.",
    "rent": "This is a rent/payment task. Draft a polite but firm message to the tenant about the outstanding payment.",
    "leasing": "This is a leasing task. Draft a message to the tenant about lease renewal or move-out logistics.",
    "compliance": "This is a compliance task. Draft a message to the relevant party (tenant or internally) requesting the needed action.",
}


def generate_task_suggestion(
    subject: str,
    context_body: str,
    category: str,
    tenant_name: str | None = None,
    property_address: str | None = None,
) -> str | None:
    """Call the LLM to generate a suggested draft message for the task.
    Returns None on any error so task creation always succeeds."""
    if not _LLM_API_KEY:
        return None
    try:
        import litellm

        category_hint = _CATEGORY_HINTS.get(category, "")
        parts = [f"Task: {subject}", f"Context: {context_body}"]
        if tenant_name:
            parts.append(f"Tenant: {tenant_name}")
        if property_address:
            parts.append(f"Property: {property_address}")
        if category_hint:
            parts.append(category_hint)

        response = litellm.completion(
            model=_LLM_MODEL,
            api_key=_LLM_API_KEY,
            api_base=_LLM_BASE_URL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": "\n".join(parts)},
            ],
            max_tokens=200,
            temperature=0.4,
        )
        text = response.choices[0].message.content or ""
        return text.strip() or None
    except Exception as exc:
        logger.warning("suggest: LLM call failed (%s) — skipping draft", exc)
        return None
