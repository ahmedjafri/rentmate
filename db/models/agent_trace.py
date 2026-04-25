"""Persistent trace log for agent tool calls, LLM responses, and suggestion lifecycle.

Each row belongs to exactly one ``AgentRun`` (composite ``(org_id, run_id)`` FK).
Grouping keys (``task_id``, ``conversation_id``) live on the run, not here —
filter via JOIN.
"""
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .base import Base, HasCreatorId, OrgId, PrimaryId


class AgentTrace(Base, OrgId, PrimaryId, HasCreatorId):
    __tablename__ = "agent_traces"

    timestamp = Column(DateTime, nullable=False, index=True)
    trace_type = Column(String(30), nullable=False, index=True)
    # "tool_call" | "tool_result" | "llm_reply" | "suggestion_created" | "suggestion_executed" | "error"
    source = Column(String(50), nullable=True)
    # "chat" | "assess" | "automation" | "dev_sim" | "reply_scanner"
    run_id = Column(String(36), nullable=False, index=True)
    sequence_num = Column(Integer, nullable=False, default=0)
    tool_name = Column(String(50), nullable=True)
    summary = Column(Text, nullable=False)
    detail = Column(Text, nullable=True)  # JSON blob for full args/response
    suggestion_id = Column(String(36), nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    model = Column(String(128), nullable=True)

    run = relationship(
        "AgentRun",
        primaryjoin=(
            "and_(AgentTrace.org_id==AgentRun.org_id, "
            "AgentTrace.run_id==AgentRun.id)"
        ),
        viewonly=True,
    )

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_agent_traces_org"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "run_id"],
            ["agent_runs.org_id", "agent_runs.id"],
            name="fk_agent_traces_run",
            ondelete="CASCADE",
        ),
    )
