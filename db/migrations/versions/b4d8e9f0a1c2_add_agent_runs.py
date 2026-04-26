"""add agent runs and review tables; rewire agent_traces under run_id

Revision ID: b4d8e9f0a1c2
Revises: a1b2c3d4e5f6
Create Date: 2026-04-25 12:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "b4d8e9f0a1c2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    # Wipe existing traces. We have no run_id for them and no backward-compat need.
    op.execute("TRUNCATE TABLE agent_traces")

    # 1. Run grouping table.
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, index=True),
        sa.Column("creator_id", sa.Integer, nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=False, index=True),
        sa.Column("ended_at", sa.DateTime, nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        # running | completed | errored | aborted | iteration_limit
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("trigger_input", sa.Text, nullable=True),
        sa.Column("final_response", sa.Text, nullable=True),
        sa.Column("agent_version", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("execution_path", sa.String(16), nullable=False),  # local | hosted
        sa.Column("conversation_id", sa.String(36), nullable=True, index=True),
        sa.Column("task_id", sa.String(36), nullable=True, index=True),
        sa.Column("total_input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_cost_cents", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("iteration_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.UniqueConstraint("org_id", "id", name="uq_agent_runs_org"),
        sa.ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
    )
    op.create_index("ix_agent_runs_org_started", "agent_runs", ["org_id", "started_at"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])

    # 2. Drop the old grouping columns from agent_traces — they live on
    #    agent_runs now. The migration TRUNCATE above means no data loss
    #    risk; readers must JOIN to agent_runs to filter by task/convo.
    op.drop_index("ix_agent_traces_task_id", table_name="agent_traces")
    op.drop_column("agent_traces", "task_id")
    op.drop_column("agent_traces", "conversation_id")

    # 3. Add run_id + per-trace observability columns to agent_traces.
    op.add_column(
        "agent_traces",
        sa.Column("run_id", sa.String(36), nullable=False),
    )
    op.add_column("agent_traces", sa.Column("input_tokens", sa.Integer, nullable=True))
    op.add_column("agent_traces", sa.Column("output_tokens", sa.Integer, nullable=True))
    op.add_column("agent_traces", sa.Column("model", sa.String(128), nullable=True))
    op.add_column("agent_traces", sa.Column("sequence_num", sa.Integer, nullable=False, server_default="0"))
    op.create_foreign_key(
        "fk_agent_traces_run",
        "agent_traces",
        "agent_runs",
        ["org_id", "run_id"],
        ["org_id", "id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_agent_traces_run_id", "agent_traces", ["run_id"])
    op.create_index("ix_agent_traces_run_seq", "agent_traces", ["run_id", "sequence_num"])

    # 4. Automated flags.
    op.create_table(
        "agent_run_flags",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, index=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("rule_name", sa.String(64), nullable=False, index=True),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("org_id", "id", name="uq_agent_run_flags_org"),
        sa.ForeignKeyConstraint(
            ["org_id", "run_id"],
            ["agent_runs.org_id", "agent_runs.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_agent_run_flags_run", "agent_run_flags", ["run_id"])

    # 5. Human review labels.
    op.create_table(
        "agent_run_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, index=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("reviewer", sa.String(128), nullable=False),
        sa.Column("failure_category", sa.String(64), nullable=False, index=True),
        sa.Column("severity_if_shipped", sa.String(32), nullable=False),
        sa.Column("root_cause", sa.Text, nullable=True),
        sa.Column("suggested_fix", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("became_regression_case", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("org_id", "id", name="uq_agent_run_reviews_org"),
        sa.ForeignKeyConstraint(
            ["org_id", "run_id"],
            ["agent_runs.org_id", "agent_runs.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_agent_run_reviews_run", "agent_run_reviews", ["run_id"])


def downgrade():
    op.drop_table("agent_run_reviews")
    op.drop_table("agent_run_flags")
    op.drop_index("ix_agent_traces_run_id", table_name="agent_traces")
    op.drop_index("ix_agent_traces_run_seq", table_name="agent_traces")
    op.drop_constraint("fk_agent_traces_run", "agent_traces", type_="foreignkey")
    op.drop_column("agent_traces", "sequence_num")
    op.drop_column("agent_traces", "model")
    op.drop_column("agent_traces", "output_tokens")
    op.drop_column("agent_traces", "input_tokens")
    op.drop_column("agent_traces", "run_id")
    op.add_column("agent_traces", sa.Column("conversation_id", sa.String(36), nullable=True))
    op.add_column("agent_traces", sa.Column("task_id", sa.String(36), nullable=True))
    op.create_index("ix_agent_traces_task_id", "agent_traces", ["task_id"])
    op.drop_table("agent_runs")
