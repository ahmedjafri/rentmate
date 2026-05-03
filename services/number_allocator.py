from sqlalchemy import text
from sqlalchemy.orm import Session


class NumberAllocator:
    """Allocates the next per-org, per-entity-type id for NumberedPrimaryId models.

    Uses a single atomic UPSERT against `id_sequences` so concurrent allocations
    cannot produce duplicates. Callers assign the returned id explicitly on the
    ORM object before flushing.
    """

    @staticmethod
    def allocate_next(sess: Session, *, entity_type: str, org_id: int) -> int:
        result = sess.execute(
            text(
                """
                INSERT INTO id_sequences (org_id, entity_type, last_number)
                VALUES (:org_id, :entity_type, 1)
                ON CONFLICT (org_id, entity_type)
                DO UPDATE SET last_number = id_sequences.last_number + 1
                RETURNING last_number
                """
            ),
            {"org_id": org_id, "entity_type": entity_type},
        )
        return result.scalar_one()
