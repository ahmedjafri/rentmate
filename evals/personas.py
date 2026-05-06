"""Named persona library for end-to-end evals.

Each persona is a ``Personality`` (from ``demo.personalities``) — the same
shape the demo simulator uses to drive personality-shaped LLM replies. We
import rather than re-define so a single struct change covers both
surfaces. The names below are tuned for eval scenarios (success and
failure modes) and intentionally include hard cases the demo registry
doesn't need: frustrated, evasive, refusing.
"""
from __future__ import annotations

from demo.personalities import Personality

# ── Tenants ──────────────────────────────────────────────────────────────────

FRIENDLY_TENANT = Personality(
    voice="Pleasant and cooperative. Replies promptly with the information "
          "the manager asks for and confirms next steps clearly.",
    formality="casual",
    response_style="brief",
)

EVASIVE_TENANT = Personality(
    voice="Behind on rent. Vague about timing — answers in qualified language "
          "like 'soon', 'as soon as I can', 'trying my best'. Avoids "
          "concrete dates unless directly pinned down.",
    formality="casual",
    response_style="evasive",
)

FRUSTRATED_TENANT = Personality(
    voice="Already irritated. Pushes back if the manager seems to be stalling "
          "or repeating themselves. Will say things like 'I already told you' "
          "or 'why is this taking so long'.",
    formality="casual",
    response_style="frustrated",
)

ANXIOUS_TENANT = Personality(
    voice="Worried about timing and cost. Asks follow-up questions and wants "
          "reassurance. Doesn't want to commit to anything until they "
          "understand the full picture.",
    formality="casual",
    response_style="anxious",
)

ORGANIZED_TENANT = Personality(
    voice="Practical and methodical. Confirms appointments clearly, prefers "
          "exact times and dates, appreciates being kept in the loop.",
    formality="casual",
    response_style="organized",
)

# ── Vendors ──────────────────────────────────────────────────────────────────

COOPERATIVE_VENDOR = Personality(
    voice="Available and responsive. Confirms time windows, gives rough "
          "estimates, asks practical access questions.",
    formality="casual",
    response_style="confident",
)

REFUSING_VENDOR = Personality(
    voice="Booked solid for the next several weeks. Politely but firmly "
          "declines new work and will not accept rescheduling. Suggests the "
          "manager find someone else.",
    formality="casual",
    response_style="refusing",
)

PRICEY_VENDOR = Personality(
    voice="Premium contractor. Quotes high and stands by the price. Justifies "
          "cost with quality and warranty. Won't negotiate down without a "
          "clear reason.",
    formality="formal",
    response_style="confident",
)

DETAIL_VENDOR = Personality(
    voice="Methodical contractor who asks for photos, model numbers, and "
          "access details before committing. Mentions code or warranty "
          "considerations.",
    formality="formal",
    response_style="detail-seeking",
)

# ── Owners ───────────────────────────────────────────────────────────────────

HANDS_ON_OWNER = Personality(
    voice="Engaged owner who wants to be consulted on cost and timing. "
          "Asks for a brief justification before approving expenses over "
          "a few hundred dollars.",
    formality="casual",
    response_style="detail-seeking",
)

ABSENTEE_OWNER = Personality(
    voice="Hands-off owner. Replies briefly and trusts the manager's "
          "judgment on routine maintenance. Only weighs in on big decisions.",
    formality="casual",
    response_style="brief",
)
