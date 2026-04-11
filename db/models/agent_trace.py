"""Persistent trace log for agent tool calls, LLM responses, and suggestion lifecycle."""
from sqlalchemy import Column, DateTime, ForeignKeyConstraint, String, Text, UniqueConstraint

from .base import Base, HasCreatorId, OrgId, PrimaryId


class AgentTrace(Base, OrgId, PrimaryId, HasCreatorId):
    __tablename__ = "agent_traces"

    timestamp = Column(DateTime, nullable=False, index=True)
    trace_type = Column(String(30), nullable=False, index=True)
    # "tool_call" | "tool_result" | "llm_reply" | "suggestion_created" | "suggestion_executed" | "error"
    source = Column(String(50), nullable=True)
    # "chat" | "assess" | "automation" | "dev_sim" | "reply_scanner"
    task_id = Column(String(36), nullable=True, index=True)
    conversation_id = Column(String(36), nullable=True)
    tool_name = Column(String(50), nullable=True)
    summary = Column(Text, nullable=False)
    detail = Column(Text, nullable=True)  # JSON blob for full args/response
    suggestion_id = Column(String(36), nullable=True)

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_agent_traces_org"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
    )
