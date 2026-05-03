from sqlalchemy.orm import Session

from db.models import Routine
from integrations.local_auth import resolve_org_id


class RoutineService:
    @staticmethod
    def get_by_id(sess: Session, routine_id: int) -> Routine | None:
        """Fetch a routine by its per-org integer id, scoped to the current org."""
        return (
            sess.query(Routine)
            .filter_by(id=routine_id, org_id=resolve_org_id())
            .first()
        )
