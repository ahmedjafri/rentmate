from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, String

from .base import Base, HasAccountId


class AutomationRevision(Base, HasAccountId):
    """
    A snapshot of the automation config, stored as a linked list (git-like versioning).
    """
    __tablename__ = "automation_revisions"

    id = Column(String(16), primary_key=True)        # sha256[:16] of content+timestamp
    config = Column(JSON, nullable=False)             # full config snapshot
    message = Column(String(500), nullable=False, default="Update automation config")
    parent_id = Column(String(16), nullable=True)    # previous revision id (soft link)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
