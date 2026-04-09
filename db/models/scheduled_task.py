import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from .base import Base, HasCreatorId


class ScheduledTask(Base, HasCreatorId):
    """A recurring or one-shot task executed by the AI agent on a schedule.

    Replaces the old Property-Flow DSL automation system with natural language
    prompts executed on cron schedules.
    """
    __tablename__ = "scheduled_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    name = Column(String(255), nullable=False)
    prompt = Column(Text, nullable=False)  # Natural language task for the agent

    # Schedule: cron expression ("0 9 * * 1"), interval ("every 4h"), or one-shot ("30m")
    schedule = Column(String(100), nullable=False)
    # Human-readable display: "Every Monday at 9am"
    schedule_display = Column(String(255), nullable=True)

    enabled = Column(Boolean, nullable=False, default=True)
    state = Column(String(20), nullable=False, default="scheduled")  # scheduled | paused | completed

    # null = run forever, N = run N times then complete
    repeat = Column(Integer, nullable=True)
    completed_count = Column(Integer, nullable=False, default=0)

    next_run_at = Column(DateTime, nullable=True)
    last_run_at = Column(DateTime, nullable=True)
    last_status = Column(String(20), nullable=True)  # ok | error
    last_output = Column(Text, nullable=True)  # Agent's response from last run

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
