"""rename_conversation_type_task_to_task_ai

Revision ID: 53fa7d80d9ab
Revises: d6e7f8a9b0c1
Create Date: 2026-03-30 05:53:41.164985

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '53fa7d80d9ab'
down_revision: Union[str, Sequence[str], None] = 'd6e7f8a9b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE conversations SET conversation_type = 'task_ai' WHERE conversation_type = 'task'")


def downgrade() -> None:
    op.execute("UPDATE conversations SET conversation_type = 'task' WHERE conversation_type = 'task_ai'")
