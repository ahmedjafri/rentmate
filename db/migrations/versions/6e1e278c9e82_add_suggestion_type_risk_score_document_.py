"""Add suggestion_type, risk_score, document_id to suggestions

Revision ID: 6e1e278c9e82
Revises: 77b0816e014c
Create Date: 2026-04-09 03:24:07.850311

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6e1e278c9e82'
down_revision: Union[str, Sequence[str], None] = '77b0816e014c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('suggestions') as batch_op:
        batch_op.add_column(sa.Column('suggestion_type', sa.String(length=20), nullable=False, server_default='maintenance'))
        batch_op.add_column(sa.Column('risk_score', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('document_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key('fk_suggestions_document_id', 'documents', ['document_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('suggestions') as batch_op:
        batch_op.drop_constraint('fk_suggestions_document_id', type_='foreignkey')
        batch_op.drop_column('document_id')
        batch_op.drop_column('risk_score')
        batch_op.drop_column('suggestion_type')
