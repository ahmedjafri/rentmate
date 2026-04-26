from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Enum as SqlEnum, ForeignKeyConstraint, Integer, String, Text
from sqlalchemy.orm import declared_attr

from db.enums import RoutineState

from .base import Base, HasCreatorId, NumberedPrimaryId, OrgId


class Routine(Base, OrgId, NumberedPrimaryId, HasCreatorId):
    """A recurring or one-shot routine that runs the AI agent on a schedule."""
    __tablename__ = "routines"

    name = Column(String(255), nullable=False)
    prompt = Column(Text, nullable=False)  # Natural language task for the agent

    # Schedule: cron expression ("0 9 * * 1"), interval ("every 4h"), or one-shot ("30m")
    schedule = Column(String(100), nullable=False)
    # Human-readable display: "Every Monday at 9am"
    schedule_display = Column(String(255), nullable=True)

    is_default = Column(Boolean, nullable=False, default=False)  # True for system-seeded tasks
    enabled = Column(Boolean, nullable=False, default=False)
    state = Column(
        SqlEnum(RoutineState, name="routine_state_enum"),
        nullable=False,
        default=RoutineState.SCHEDULED,
    )

    # null = run forever, N = run N times then complete
    repeat = Column(Integer, nullable=True)
    completed_count = Column(Integer, nullable=False, default=0)

    next_run_at = Column(DateTime, nullable=True)
    last_run_at = Column(DateTime, nullable=True)
    last_status = Column(String(20), nullable=True)  # ok | error
    last_output = Column(Text, nullable=True)  # Agent's response from last run
    simulated_at = Column(DateTime, nullable=True)  # Must simulate before enabling

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    @declared_attr
    def __table_args__(cls):
        return (
            NumberedPrimaryId.primary_key(cls),
            ForeignKeyConstraint(
                ["org_id", "creator_id"],
                ["users.org_id", "users.id"],
            ),
        )
