"""
Generate an initial outreach message to a vendor for a newly assigned task.
Returns the message text, or None if generation fails.
"""
import logging
import os

logger = logging.getLogger("rentmate.vendor_outreach")

_SYSTEM = (
    "You are RentMate, a property management AI assistant. "
    "You are reaching out to a vendor on behalf of a property manager. "
    "This is the FIRST message in the conversation — you are inquiring, not instructing. "
    "Your goal is to ask the vendor about their availability and pricing for the job. "
    "Briefly describe the work needed and the property location, then ask for "
    "their availability and a quote. "
    "Be succinct — 2-3 sentences max. Be professional and friendly. "
    "Do not include a subject line. Do not add placeholders like [Name] — use the "
    "actual names if provided, or write generically if not. "
    "Do not explain what you are doing; just write the message.\n\n"
    "PRIVACY: Never include tenant personal information in vendor messages. "
    "Do not share tenant names, email addresses, phone numbers, lease details, "
    "or rent amounts with vendors. Refer to tenants generically as 'the tenant'. "
    "Tell the vendor to coordinate access through the property manager, not directly with the tenant."
)


def generate_vendor_outreach(
    task_title: str,
    task_body: str,
    category: str | None = None,
    vendor_name: str | None = None,
    context: str | None = None,
) -> str | None:
    """Call the LLM to generate a vendor outreach message.
    Returns None on any error so task creation always succeeds."""
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return None
    try:
        import litellm

        parts = [f"Task: {task_title}"]
        if task_body:
            parts.append(f"Details: {task_body}")
        if category:
            parts.append(f"Category: {category}")
        if vendor_name:
            parts.append(f"Vendor: {vendor_name}")
        if context:
            parts.append(f"Property context:\n{context}")

        response = litellm.completion(
            model=os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
            api_key=api_key,
            api_base=os.getenv("LLM_BASE_URL") or None,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": "\n".join(parts)},
            ],
            max_tokens=150,
            temperature=0.4,
        )
        text = response.choices[0].message.content or ""
        return text.strip() or None
    except Exception as exc:
        logger.warning("vendor_outreach: LLM call failed (%s) — skipping", exc)
        return None
