from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import User
from integrations.local_auth import resolve_account_id, resolve_org_id

OWNER_USER_TYPE = "owner"


def get_owner_query(sess: Session):
    return select(User).where(
        User.user_type == OWNER_USER_TYPE,
        User.org_id == resolve_org_id(),
        User.creator_id == resolve_account_id(),
    )


def get_owner_by_external_id(sess: Session, uid: str) -> User | None:
    return sess.execute(get_owner_query(sess).where(User.external_id == uid)).scalar_one_or_none()


def get_owner_by_id(sess: Session, owner_id: int) -> User | None:
    return sess.execute(get_owner_query(sess).where(User.id == owner_id)).scalar_one_or_none()
