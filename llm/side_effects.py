"""Materialize agent side-effects into database rows.

The hosted agent returns structured side-effects (suggestions, vendor
creation) instead of writing to the DB directly.  This module processes
those side-effects after the AI reply is persisted, so suggestion messages
appear below the agent response in the conversation timeline.
"""
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from db.enums import AgentSource, SuggestionOption
from db.models import MessageType
from gql.services import suggestion_service
from gql.services.chat_service import parse_message_meta, send_message


def _flushed_message_payload(msg) -> dict[str, Any]:
    meta = parse_message_meta(getattr(msg, "meta", None))
    return {
        "id": str(msg.id),
        "body": msg.body,
        "message_type": (getattr(msg.message_type, "name", str(msg.message_type or "")).lower() if msg.message_type else "message"),
        "suggestion_id": str(meta.related_task_ids.suggestion_id) if meta.related_task_ids and meta.related_task_ids.suggestion_id is not None else None,
        "action_card": meta.action_card.model_dump(exclude_none=True) if meta.action_card else None,
    }


def process_side_effects(
    db: Session,
    *, side_effects: list[dict],
    conversation_id: str,
    base_time: datetime,
) -> list[dict]:
    """Create DB rows for each side-effect and return the created chat messages."""
    flushed: list[dict] = []
    offset = 0

    for effect in side_effects:
        effect_type = effect.get("type", "")

        if effect_type == "chat_message":
            offset += 1
            conv_id = effect.get("conversation_id", conversation_id)
            msg = send_message(
                db,
                conversation_id=conv_id,
                body=effect["body"],
                message_type=effect.get("message_type", MessageType.MESSAGE),
                sender_name=effect.get("sender_name", "RentMate"),
                is_ai=effect.get("is_ai", True),
                draft_reply=effect.get("draft_reply"),
                related_task_ids=effect.get("related_task_ids"),
                meta=effect.get("meta"),
                sent_at=base_time + timedelta(milliseconds=offset),
            )
            flushed.append(_flushed_message_payload(msg))

        elif effect_type == "suggestion_message":
            # Local fallback: pre-built message params from pending chat messages
            offset += 1
            conv_id = effect.get("conversation_id", conversation_id)
            msg = send_message(
                db,
                conversation_id=conv_id,
                body=effect["body"],
                message_type=effect.get("message_type", MessageType.SUGGESTION),
                sender_name=effect.get("sender_name", "RentMate"),
                is_ai=effect.get("is_ai", True),
                draft_reply=effect.get("draft_reply"),
                related_task_ids=effect.get("related_task_ids"),
                meta=effect.get("meta"),
                sent_at=base_time + timedelta(milliseconds=offset),
            )
            flushed.append(_flushed_message_payload(msg))

        elif effect_type.endswith("_suggestion") or effect_type == "create_suggestion":
            # Hosted agent: structured suggestion data
            offset += 1
            options = [
                SuggestionOption(**o) for o in (effect.get("options") or [])
            ]
            suggestion = suggestion_service.create_suggestion(
                db,
                title=effect["title"],
                ai_context=effect.get("ai_context", effect["title"]),
                category=effect.get("category"),
                urgency=effect.get("urgency"),
                source=AgentSource(),
                options=options,
                action_payload=effect.get("action_payload"),
                property_id=effect.get("property_id"),
            )
            if effect.get("task_id"):
                suggestion.task_id = effect["task_id"]

            # Build message body
            body_parts = [effect["title"]]
            payload = effect.get("action_payload") or {}
            if payload.get("vendor_name"):
                body_parts.append(f"Vendor: {payload['vendor_name']}")
            if payload.get("draft_message"):
                body_parts.append(f"Draft: {payload['draft_message'][:200]}")

            msg = send_message(
                db,
                conversation_id=conversation_id,
                body="\n".join(body_parts),
                message_type=effect.get("message_type", MessageType.ACTION),
                sender_name="RentMate",
                is_ai=True,
                draft_reply=payload.get("draft_message"),
                related_task_ids={"suggestion_id": suggestion.id},
                meta=effect.get("meta") or {
                    "action_card": {
                        "kind": "suggestion",
                        "title": effect["title"],
                        "summary": effect.get("ai_context", effect["title"]),
                        "fields": [
                            {"label": "Category", "value": str(effect["category"]).title()}
                            for _ in [None] if effect.get("category")
                        ] + [
                            {"label": "Urgency", "value": str(effect["urgency"]).title()}
                            for _ in [None] if effect.get("urgency")
                        ],
                        "links": [{
                            "label": "Open suggestion",
                            "entity_type": "suggestion",
                            "entity_id": str(suggestion.id),
                        }],
                        "units": [],
                    },
                },
                sent_at=base_time + timedelta(milliseconds=offset),
            )
            flushed.append(_flushed_message_payload(msg))

        elif effect_type == "create_vendor":
            from gql.services.vendor_service import VendorService
            from gql.types import CreateVendorInput
            VendorService.create_vendor(db, CreateVendorInput(
                name=effect["name"],
                phone=effect.get("phone", ""),
                company=effect.get("company"),
                vendor_type=effect.get("vendor_type"),
                email=effect.get("email"),
                contact_method="rentmate",
            ))

    return flushed
