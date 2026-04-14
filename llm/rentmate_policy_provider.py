from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.memory_provider import MemoryProvider

POLICY_DIR = Path(__file__).parent / "policies"


@dataclass(frozen=True)
class PolicyRule:
    key: str
    title: str
    filename: str
    keywords: tuple[str, ...]

    @property
    def text(self) -> str:
        return (POLICY_DIR / self.filename).read_text()


POLICIES: tuple[PolicyRule, ...] = (
    PolicyRule(
        key="communication",
        title="Communication",
        filename="communication.md",
        keywords=(
            "angry", "frustrated", "delay", "rude", "upset", "hospital", "sick",
            "ill", "late fee", "waive", "hardship", "payment", "rent",
        ),
    ),
    PolicyRule(
        key="coordination",
        title="Coordination",
        filename="coordination.md",
        keywords=(
            "vendor", "contractor", "plumber", "roofer", "electrician", "quote",
            "schedule", "appointment", "time", "access", "tenant", "coordinate",
        ),
    ),
    PolicyRule(
        key="maintenance",
        title="Maintenance",
        filename="maintenance.md",
        keywords=(
            "maintenance", "repair", "vendor", "contractor", "plumber", "hvac",
            "electrician", "roofer", "leak", "burst pipe", "flooding", "gas smell",
            "no heat", "heater", "sparks", "electrical", "emergency",
        ),
    ),
    PolicyRule(
        key="owner_approval",
        title="Owner Approval",
        filename="owner_approval.md",
        keywords=(
            "quote", "$", "quoted", "approval", "approve", "proceed",
            "expensive", "high quote", "comparison quote", "second quote",
            "review and respond",
        ),
    ),
    PolicyRule(
        key="batch_operations",
        title="Batch Operations",
        filename="batch_operations.md",
        keywords=(
            "all properties", "all washington properties", "all washington state properties",
            "all wa properties", "all matching properties", "each property", "for all of them",
            "across all", "every property",
        ),
    ),
    PolicyRule(
        key="rent_collection",
        title="Rent Collection",
        filename="rent_collection.md",
        keywords=(
            "rent", "late fee", "late rent", "payment", "partial payment", "payment plan",
            "waive", "waiver", "hospital", "hardship", "friday", "next month", "pay $",
        ),
    ),
    PolicyRule(
        key="legal_compliance",
        title="Legal / Compliance",
        filename="legal_compliance.md",
        keywords=(
            "eviction", "notice", "14-day", "14 day", "pay or vacate", "unlawful detainer",
            "lawyer", "counsel", "court", "file", "filing", "compliance", "owed",
            "hasn't paid", "has not paid", "non-payment", "non payment",
        ),
    ),
    PolicyRule(
        key="leasing",
        title="Leasing",
        filename="leasing.md",
        keywords=(
            "prospect", "showing", "smoking", "smoke", "application", "screening",
            "available", "see the unit", "tour", "interested in the unit",
        ),
    ),
    PolicyRule(
        key="move_out",
        title="Move Out",
        filename="move_out.md",
        keywords=(
            "move out", "moving out", "30-day notice", "30 day notice", "vacate",
            "key return", "final inspection", "security deposit", "deposit back",
        ),
    ),
    PolicyRule(
        key="safety",
        title="Safety",
        filename="safety.md",
        keywords=(
            "suicide", "self-harm", "self harm", "kill myself", "emergency", "gas smell",
            "flood", "burst pipe", "no heat", "shock", "electrical", "danger",
            "can't take this anymore", "cant take this anymore", "no point in going on",
        ),
    ),
    PolicyRule(
        key="fair_housing",
        title="Fair Housing",
        filename="fair_housing.md",
        keywords=(
            "disability", "accommodation", "service animal", "children", "kids",
            "family status", "pregnant", "wheelchair", "religion", "race",
        ),
    ),
)


class RentmatePolicyProvider(MemoryProvider):
    def __init__(self) -> None:
        self._session_id = ""
        self._hermes_home = Path(".")
        self._events_path = Path(".")

    @property
    def name(self) -> str:
        return "rentmate_policy"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        hermes_home = kwargs.get("hermes_home")
        self._hermes_home = Path(str(hermes_home)).expanduser().resolve() if hermes_home else Path(".").resolve()
        self._events_path = self._hermes_home / "policy_events.jsonl"

    def system_prompt_block(self) -> str:
        return (
            "## RentMate Constitution\n\n"
            "- Apply broad principles before workflow scripts.\n"
            "- Use the most relevant policy context for the current request.\n"
            "- Favor clear next steps, low-conflict communication, privacy protection, and legal caution.\n"
            "- If a request is high-risk, respond conservatively and do not guess missing legal or factual details.\n"
            "- The user-facing artifact must embody the policy itself: empathy belongs in the tenant message, legal sequencing belongs in the manager reply, and batch work should name the entities covered.\n"
            "- When concrete facts are already known from task context, keep those facts in the final reply itself instead of collapsing to a meta-summary such as 'I've answered' or 'I closed the task.'\n"
            "- When a manager reports a fresh operational update such as a notice being served, a document being uploaded, or a payment being made, treat that report as the current workflow state unless directly contradicted by stronger evidence.\n"
            "- When a repair issue is known, name the actual issue in the final reply instead of replacing it with a generic phrase like 'repair request.'\n"
            "- For expensive repairs or high quotes, the final reply must say plainly that manager or owner approval is still pending before proceeding.\n"
            "- If a legal or eviction-related reply mentions notices, filing, or eviction, explicitly frame the step as manager-directed, manager-reviewed, or escalated. Do not let legal wording read like an autonomous threat.\n"
            "- When the user scopes work to a subset, the final reply should behave like filtered results: include the matching entities and stay silent about excluded ones unless the user explicitly asks what was left out.\n"
            "- For filtered subsets, do not add explanatory phrases like 'excluded as requested' or 'skipped the non-matching property.' Non-matching items should be invisible in the final reply.\n"
            "- For batch subset work, mentally scan the final reply once before sending it and remove any mention of excluded entities.\n"
            "- In subset replies, never use words like 'excluded', 'skipped', 'non-matching', or 'outside the subset' unless the user explicitly asks for that comparison.\n"
            "- Preferred subset pattern: name the matching entities and the action taken, then stop. Do not append a sentence about what was left out.\n"
            "- When multiple policies apply, follow the highest-risk rule first: safety before legal, legal before approval, approval before coordination, coordination before convenience."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        tags = select_policy_keys(query)
        if not tags:
            return ""
        blocks: list[str] = []
        for policy in POLICIES:
            if policy.key in tags:
                blocks.append(policy.text.strip())
        return "\n\n".join(blocks)

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        try:
            event = {
                "session_id": session_id or self._session_id,
                "tags": list(select_policy_keys(user_content)),
                "user": user_content[:500],
                "assistant": assistant_content[:500],
            }
            with self._events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")
        except Exception:
            pass

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return []


def select_policy_keys(query: str) -> tuple[str, ...]:
    text = (query or "").lower()
    scored: list[tuple[int, str]] = []
    for policy in POLICIES:
        score = sum(keyword in text for keyword in policy.keywords)
        if score:
            scored.append((score, policy.key))
    ordered = [key for _, key in sorted(scored, key=lambda item: (-item[0], _policy_order(item[1])))]
    tags = list(dict.fromkeys(ordered))
    if not tags and text:
        tags = ["communication"]
    if "safety" in tags and "communication" not in tags:
        tags.append("communication")
    if "legal_compliance" in tags and "communication" not in tags:
        tags.append("communication")
    if "owner_approval" in tags and "communication" not in tags:
        tags.append("communication")
    if "rent_collection" in tags and "communication" not in tags:
        tags.append("communication")
    if "batch_operations" in tags and "coordination" not in tags:
        tags.append("coordination")
    if "maintenance" in tags and "coordination" not in tags:
        tags.append("coordination")
    if "maintenance" in tags and "communication" not in tags:
        tags.append("communication")
    if "leasing" in tags and "communication" not in tags:
        tags.append("communication")
    if "move_out" in tags and "communication" not in tags:
        tags.append("communication")
    return tuple(tags)


def _policy_order(key: str) -> int:
    order = [
        "safety",
        "fair_housing",
        "legal_compliance",
        "maintenance",
        "rent_collection",
        "move_out",
        "leasing",
        "owner_approval",
        "batch_operations",
        "coordination",
        "communication",
    ]
    try:
        return order.index(key)
    except ValueError:
        return len(order)
