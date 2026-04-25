"""Persistent grouping of one agent invocation: tokens, cost, status, traces."""
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from .base import Base, HasCreatorId, OrgId, PrimaryId  # noqa: F401


class AgentRun(Base, OrgId, PrimaryId, HasCreatorId):
    """One end-to-end agent invocation — every turn + tool call shares this row.

    Status values: ``running | completed | errored | aborted | iteration_limit``.
    """

    __tablename__ = "agent_runs"

    started_at = Column(DateTime, nullable=False, index=True)
    ended_at = Column(DateTime, nullable=True)
    status = Column(String(16), nullable=False)
    source = Column(String(50), nullable=False)
    # chat | assess | task_review | routine | simulate | dev_sim | reply_scanner
    trigger_input = Column(Text, nullable=True)
    final_response = Column(Text, nullable=True)
    agent_version = Column(String(64), nullable=False)
    prompt_version = Column(String(64), nullable=True)
    model = Column(String(128), nullable=True)
    execution_path = Column(String(16), nullable=False)  # local | hosted
    conversation_id = Column(String(36), nullable=True, index=True)
    task_id = Column(String(36), nullable=True, index=True)
    total_input_tokens = Column(Integer, nullable=False, default=0)
    total_output_tokens = Column(Integer, nullable=False, default=0)
    total_cost_cents = Column(Numeric(10, 4), nullable=False, default=0)
    iteration_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    run_metadata = Column("metadata", JSONB, nullable=False, default=dict)

    traces = relationship(
        "AgentTrace",
        primaryjoin=(
            "and_(AgentTrace.org_id==AgentRun.org_id, "
            "AgentTrace.run_id==AgentRun.id)"
        ),
        viewonly=True,
        order_by="AgentTrace.sequence_num",
    )

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_agent_runs_org"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
    )


class AgentRunFlag(Base, OrgId, PrimaryId):
    __tablename__ = "agent_run_flags"

    run_id = Column(String(36), nullable=False)
    rule_name = Column(String(64), nullable=False, index=True)
    severity = Column(String(16), nullable=False)
    details = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_agent_run_flags_org"),
        ForeignKeyConstraint(
            ["org_id", "run_id"],
            ["agent_runs.org_id", "agent_runs.id"],
            ondelete="CASCADE",
        ),
    )


class AgentRunReview(Base, OrgId, PrimaryId):
    __tablename__ = "agent_run_reviews"

    run_id = Column(String(36), nullable=False)
    reviewer = Column(String(128), nullable=False)
    failure_category = Column(String(64), nullable=False, index=True)
    severity_if_shipped = Column(String(32), nullable=False)
    root_cause = Column(Text, nullable=True)
    suggested_fix = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    became_regression_case = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_agent_run_reviews_org"),
        ForeignKeyConstraint(
            ["org_id", "run_id"],
            ["agent_runs.org_id", "agent_runs.id"],
            ondelete="CASCADE",
        ),
    )
