import re

_TRANSIENT_FAILURE_PATTERNS = (
    r"\btechnical issue\b",
    r"\btechnical difficulties\b",
    r"\bsystem error\b",
    r"\bcurrently unavailable\b",
    r"\btemporarily unavailable\b",
    r"\bpersistent technical issue\b",
    r"\bbrowser engine\b",
    r"\brender(?:ing|er)\b",
    r"\btool is not working\b",
    r"\btool .* unavailable\b",
    r"\bunable to create\b",
    r"\bunable to complete\b",
    r"\bencountered an error\b",
    r"\bexperiencing .* issue\b",
    r"\bcreate (?:the )?document manually\b",
    r"\bcopy (?:this|the) (?:text|html)\b",
    r"\bprint (?:it )?to pdf\b",
)


def is_transient_tool_failure_text(text: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip()).lower()
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in _TRANSIENT_FAILURE_PATTERNS)
