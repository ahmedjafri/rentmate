from sqlalchemy.orm import Session

from backends.local_auth import resolve_org_id
from db.models import Routine


class RoutineService:
    @staticmethod
    def get_by_id(sess: Session, routine_id: int) -> Routine | None:
        """Fetch a routine by its per-org integer id, scoped to the current org."""
        return (
            sess.query(Routine)
            .filter_by(id=routine_id, org_id=resolve_org_id())
            .first()
        )
