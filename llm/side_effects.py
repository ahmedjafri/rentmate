"""Materialize agent side-effects into database rows.

The hosted agent returns structured side-effects (suggestions, vendor
creation) instead of writing to the DB directly.  This module processes
those side-effects after the AI reply is persisted, so suggestion messages
appear below the agent response in the conversation timeline.
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from db.enums import AgentSource, SuggestionOption
from db.models import MessageType
from gql.services import suggestion_service
from gql.services.chat_service import send_message


def process_side_effects(
    db: Session,
    *, side_effects: list[dict],
    conversation_id: str,
    base_time: datetime,
) -> list[dict]:
    """Create DB rows for each side-effect.

    Returns a list of suggestion message dicts suitable for inclusion in the
    SSE done payload (id, body, message_type, suggestion_id).
    """
    flushed: list[dict] = []
    offset = 0

    for effect in side_effects:
        effect_type = effect.get("type", "")

        if effect_type == "suggestion_message":
            # Local fallback: pre-built message params from pending_suggestion_messages
            offset += 1
            conv_id = effect.get("conversation_id", conversation_id)
            msg = send_message(
                db, conv_id,
                body=effect["body"],
                message_type=effect.get("message_type", MessageType.SUGGESTION),
                sender_name=effect.get("sender_name", "RentMate"),
                is_ai=effect.get("is_ai", True),
                draft_reply=effect.get("draft_reply"),
                related_task_ids=effect.get("related_task_ids"),
                sent_at=base_time + timedelta(milliseconds=offset),
            )
            flushed.append({
                "id": msg.id,
                "body": msg.body,
                "message_type": "suggestion",
                "suggestion_id": (msg.related_task_ids or {}).get("suggestion_id"),
            })

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
                db, conversation_id,
                body="\n".join(body_parts),
                message_type=MessageType.SUGGESTION,
                sender_name="RentMate",
                is_ai=True,
                draft_reply=payload.get("draft_message"),
                related_task_ids={"suggestion_id": suggestion.id},
                sent_at=base_time + timedelta(milliseconds=offset),
            )
            flushed.append({
                "id": msg.id,
                "body": msg.body,
                "message_type": "suggestion",
                "suggestion_id": suggestion.id,
            })

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
