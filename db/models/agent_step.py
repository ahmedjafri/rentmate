"""ATIF v1.4-shaped step rows for agent runs.

Each ``AgentStep`` is one entry in a Harbor Framework trajectory: a
``user`` / ``agent`` / ``system`` turn with optional tool calls, an
observation block, and per-step token / cost metrics. Replaces the old
``AgentTrace`` row-per-event model where a single agent turn could fan
out to many heterogeneous traces (``tool_call``, ``tool_result``,
``error`` …).

ATIF spec: https://www.harborframework.com/docs/agents/trajectory-format
"""
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from .base import Base, HasCreatorId, OrgId, PrimaryId


class AgentStep(Base, OrgId, PrimaryId, HasCreatorId):
    """One ATIF Step inside an ``AgentRun`` trajectory.

    ``step_id`` is the ATIF-spec sequential integer (1, 2, 3, …) per run;
    rentmate's UUID ``id`` is the row primary key, so the two never
    collide. Composite FK to ``agent_runs(org_id, id)`` cascade-deletes
    steps when their owning run is removed.
    """

    __tablename__ = "agent_steps"

    run_id = Column(String(36), nullable=False, index=True)
    # ATIF: sequential int starting at 1.
    step_id = Column(Integer, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    # ATIF: "user" | "agent" | "system".
    source = Column(String(8), nullable=False)
    message = Column(Text, nullable=False)

    # Agent-step-only fields (nullable on user/system steps).
    model_name = Column(String(128), nullable=True)
    # TODO(atif): populated once the per-provider chain-of-thought shim
    # lands (Anthropic <thinking>, OpenAI reasoning_content, …).
    reasoning_content = Column(Text, nullable=True)
    # ATIF ToolCall[] — list of {tool_call_id, function_name, arguments}.
    tool_calls = Column(JSONB, nullable=True)
    # ATIF Observation — {results: [{source_call_id, content}]}.
    observation = Column(JSONB, nullable=True)
    # ATIF Metrics — {prompt_tokens, completion_tokens, cached_tokens?, cost_usd}.
    metrics = Column(JSONB, nullable=True)

    # Rentmate-internal slot for fields that don't fit ATIF (error_kind,
    # legacy trace_type, tool durations, …). Surfaced under ATIF's
    # top-level ``extra`` on serialization.
    extra = Column(JSONB, nullable=False, default=dict)

    run = relationship(
        "AgentRun",
        primaryjoin=(
            "and_(AgentStep.org_id==AgentRun.org_id, "
            "AgentStep.run_id==AgentRun.id)"
        ),
        viewonly=True,
    )

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_agent_steps_org"),
        UniqueConstraint(
            "org_id", "run_id", "step_id", name="uq_agent_steps_run_step"
        ),
        Index("ix_agent_steps_run_step", "run_id", "step_id"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "run_id"],
            ["agent_runs.org_id", "agent_runs.id"],
            name="fk_agent_steps_run",
            ondelete="CASCADE",
        ),
    )
