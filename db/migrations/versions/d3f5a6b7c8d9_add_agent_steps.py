"""add agent_steps table for ATIF v1.4 trajectory persistence

Adds the ``agent_steps`` table that maps 1:1 to a Harbor Framework ATIF
Step. The old ``agent_traces`` table is intentionally left in place — a
read-time adapter in ``llm/trajectory.py`` synthesizes ATIF trajectories
from pre-cutover trace rows, so historical runs keep rendering in
DevTools without a data migration. A follow-up migration drops
``agent_traces`` once that historical replay value expires.

Revision ID: d3f5a6b7c8d9
Revises: c2f3e4d5a6b8
Create Date: 2026-04-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d3f5a6b7c8d9"
down_revision: Union[str, None] = "c2f3e4d5a6b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, index=True),
        sa.Column("creator_id", sa.Integer, nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False, index=True),
        sa.Column("step_id", sa.Integer, nullable=False),
        sa.Column("timestamp", sa.DateTime, nullable=False, index=True),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("reasoning_content", sa.Text, nullable=True),
        sa.Column("tool_calls", postgresql.JSONB, nullable=True),
        sa.Column("observation", postgresql.JSONB, nullable=True),
        sa.Column("metrics", postgresql.JSONB, nullable=True),
        sa.Column(
            "extra", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_agent_steps_org"),
        sa.UniqueConstraint(
            "org_id", "run_id", "step_id", name="uq_agent_steps_run_step"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "run_id"],
            ["agent_runs.org_id", "agent_runs.id"],
            name="fk_agent_steps_run",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_agent_steps_run_step", "agent_steps", ["run_id", "step_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_steps_run_step", table_name="agent_steps")
    op.drop_table("agent_steps")
