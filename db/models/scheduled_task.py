from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint

from .base import Base, HasCreatorId, OrgId, PrimaryId


class ScheduledTask(Base, OrgId, PrimaryId, HasCreatorId):
    """A recurring or one-shot task executed by the AI agent on a schedule.

    Replaces the old Property-Flow DSL automation system with natural language
    prompts executed on cron schedules.
    """
    __tablename__ = "scheduled_tasks"

    name = Column(String(255), nullable=False)
    prompt = Column(Text, nullable=False)  # Natural language task for the agent

    # Schedule: cron expression ("0 9 * * 1"), interval ("every 4h"), or one-shot ("30m")
    schedule = Column(String(100), nullable=False)
    # Human-readable display: "Every Monday at 9am"
    schedule_display = Column(String(255), nullable=True)

    is_default = Column(Boolean, nullable=False, default=False)  # True for system-seeded tasks
    enabled = Column(Boolean, nullable=False, default=True)
    state = Column(String(20), nullable=False, default="scheduled")  # scheduled | paused | completed

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

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_scheduled_tasks_org"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
    )
