"""Personality registry for the demo simulator.

Maps seeded tenant/vendor phone numbers to a small ``Personality`` record
that shapes the LLM-drafted reply. Keyed on phone (not name) so the
registry stays in sync with ``demo/seed.py`` even if names change.

Used only by ``demo/simulator.py`` — never imported in production code
paths. Adding a new seeded user without an entry here just falls back to
``DEFAULT_PERSONALITY`` rather than crashing.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Personality:
    voice: str           # 1-2 sentences fed into the system prompt
    formality: str       # "casual" | "formal"
    response_style: str  # short label: "brief", "chatty", "anxious", etc.


DEFAULT_PERSONALITY = Personality(
    voice="Friendly and reasonable. Replies in 1-2 short sentences.",
    formality="casual",
    response_style="brief",
)


# Tenants — phones from demo/seed.py:TENANTS
_TENANT_PERSONALITIES: dict[str, Personality] = {
    "+14255550101": Personality(  # Marcus Johnson
        voice="Easy-going professional. Replies in short sentences. "
              "Doesn't sweat small stuff but follows through.",
        formality="casual",
        response_style="brief",
    ),
    "+14255550102": Personality(  # Priya Patel
        voice="Detail-oriented, asks clarifying questions. Polite but "
              "expects timelines and won't accept vague answers.",
        formality="casual",
        response_style="detail-seeking",
    ),
    "+14255550103": Personality(  # Devon Torres
        voice="Laid-back and easygoing. Sometimes forgets to reply right "
              "away. When he does, he's brief and friendly.",
        formality="casual",
        response_style="brief",
    ),
    "+14255550104": Personality(  # Aisha Williams
        voice="Practical and organized. Confirms appointments clearly and "
              "appreciates being kept in the loop.",
        formality="casual",
        response_style="organized",
    ),
    "+14255550105": Personality(  # Ryan Chen
        voice="Tech-savvy and direct. Prefers texts over calls. Asks for "
              "links and exact instructions.",
        formality="casual",
        response_style="brief",
    ),
    "+14255550106": Personality(  # Sofia Martinez
        voice="Warm and chatty, sometimes adds context the manager didn't "
              "ask for. Polite and grateful.",
        formality="casual",
        response_style="chatty",
    ),
    "+14255550107": Personality(  # Tyler Brooks
        voice="Slightly anxious renter. Asks follow-up questions about "
              "timing and cost, wants reassurance.",
        formality="casual",
        response_style="anxious",
    ),
    "+14255550108": Personality(  # Nadia Kim
        voice="Quiet and professional. Polite, brief, no small talk.",
        formality="formal",
        response_style="brief",
    ),
}


# Vendors — phones from demo/seed.py:VENDORS
_VENDOR_PERSONALITIES: dict[str, Personality] = {
    "+14255550201": Personality(  # Mike Rivera (plumbing)
        voice="Veteran plumber, no-nonsense. Quotes confidently and "
              "commits to time windows. Uses 'yep', 'sounds good'.",
        formality="casual",
        response_style="confident",
    ),
    "+14255550202": Personality(  # Karen Walsh (electric)
        voice="Careful and methodical electrician. Asks for photos before "
              "quoting. Mentions code compliance.",
        formality="formal",
        response_style="detail-seeking",
    ),
    "+14255550203": Personality(  # James Park (HVAC)
        voice="Professional HVAC tech. Replies promptly with availability "
              "and rough estimates. Likes confirming access details.",
        formality="formal",
        response_style="organized",
    ),
    "+14255550204": Personality(  # Luis Gomez (handyman)
        voice="Friendly handyman, flexible on time. Sometimes forgets "
              "details and asks the manager to repeat them.",
        formality="casual",
        response_style="chatty",
    ),
    "+14255550205": Personality(  # Sarah Chen (landscaper)
        voice="Established landscaper. Quotes flat-rate where she can, "
              "explains seasonal timing tradeoffs.",
        formality="casual",
        response_style="organized",
    ),
    "+14255550206": Personality(  # Alex Nakamura (landscaper)
        voice="Newer landscaper, eager to win the job. Offers competitive "
              "pricing and quick turnaround.",
        formality="casual",
        response_style="confident",
    ),
}


PERSONALITIES: dict[str, Personality] = {
    **_TENANT_PERSONALITIES,
    **_VENDOR_PERSONALITIES,
}


def get_personality_by_phone(phone: str | None) -> Personality:
    """Return the personality for a phone number, falling back to a safe
    default rather than raising. Returning a default keeps the simulator
    robust to seed.py changes without requiring a registry update."""
    if not phone:
        return DEFAULT_PERSONALITY
    return PERSONALITIES.get(phone.strip(), DEFAULT_PERSONALITY)


def is_seeded_phone(phone: str | None) -> bool:
    """True iff this phone is in the registry. Used to gate which conversations
    the simulator touches — non-seeded users (e.g. a real tester poking the
    dev DB) are left alone."""
    if not phone:
        return False
    return phone.strip() in PERSONALITIES
