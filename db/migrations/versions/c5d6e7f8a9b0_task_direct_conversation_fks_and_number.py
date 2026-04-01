"""task direct conversation FKs and task_number

Revision ID: c5d6e7f8a9b0
Revises: b3c4d5e6f7a8, a1b2c3d4e5f6
Create Date: 2026-03-29

Merges the task-table branch and the conversation-taxonomy branch.

Changes:
- Add tasks.task_number (per-account sequential integer)
- Add tasks.ai_conversation_id FK → conversations (the task's AI thread)
- Add tasks.parent_conversation_id FK → conversations (originating tenant/vendor thread)
- Populate ai_conversation_id from existing conversations.task_id
- Populate parent_conversation_id from spawned conversations
- Assign task_number per account ordered by created_at
- Make tasks.account_id NOT NULL (backfill sentinel for any NULLs)
- Drop conversations.task_id (relationship now inverted to tasks.ai_conversation_id)
- Drop conversations.ancestor_ids (dead data, never read)
"""
from alembic import op
import sqlalchemy as sa

revision = 'c5d6e7f8a9b0'
down_revision = ('b3c4d5e6f7a8', 'a1b2c3d4e5f6')
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. New columns on tasks
    op.add_column('tasks', sa.Column('task_number',            sa.Integer(),  nullable=True))
    op.add_column('tasks', sa.Column('ai_conversation_id',     sa.String(36), nullable=True))
    op.add_column('tasks', sa.Column('parent_conversation_id', sa.String(36), nullable=True))

    # 2. Add FKs for the new conversation columns
    op.create_foreign_key(
        'fk_tasks_ai_convo', 'tasks', 'conversations',
        ['ai_conversation_id'], ['id'], ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_tasks_parent_convo', 'tasks', 'conversations',
        ['parent_conversation_id'], ['id'], ondelete='SET NULL',
    )

    # 3. Populate ai_conversation_id from existing task-linked conversations
    if conn.dialect.name == 'postgresql':
        conn.execute(sa.text("""
            UPDATE tasks t SET ai_conversation_id = (
                SELECT id FROM conversations WHERE task_id = t.id LIMIT 1
            )
        """))

        # 4. Populate parent_conversation_id for spawned tasks
        conn.execute(sa.text("""
            UPDATE tasks t SET parent_conversation_id = (
                SELECT parent_conversation_id FROM conversations
                WHERE task_id = t.id AND parent_conversation_id IS NOT NULL LIMIT 1
            )
        """))

        # 5. Assign task_number per account ordered by created_at
        conn.execute(sa.text("""
            UPDATE tasks t SET task_number = sub.rn
            FROM (
                SELECT id,
                       ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY created_at) AS rn
                FROM tasks
            ) sub WHERE t.id = sub.id
        """))

    # 6. Make account_id NOT NULL (backfill sentinel if any NULLs exist)
    conn.execute(sa.text(
        "UPDATE tasks SET account_id = '00000000-0000-0000-0000-000000000001' "
        "WHERE account_id IS NULL"
    ))
    op.alter_column('tasks', 'account_id', nullable=False)

    # 7. Unique constraint on (account_id, task_number)
    op.create_unique_constraint('uq_task_number_per_account', 'tasks', ['account_id', 'task_number'])

    # 8. Drop task_id and ancestor_ids from conversations
    if conn.dialect.name == 'postgresql':
        op.drop_constraint('fk_conversations_task_id_tasks', 'conversations', type_='foreignkey')
    op.drop_column('conversations', 'task_id')
    op.drop_column('conversations', 'ancestor_ids')


def downgrade():
    op.add_column('conversations', sa.Column('task_id',      sa.String(36), nullable=True))
    op.add_column('conversations', sa.Column('ancestor_ids', sa.JSON(),     nullable=True))

    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        # Re-populate task_id from tasks.ai_conversation_id
        conn.execute(sa.text("""
            UPDATE conversations c SET task_id = (
                SELECT id FROM tasks WHERE ai_conversation_id = c.id LIMIT 1
            )
        """))
        op.create_foreign_key(
            'fk_conversations_task_id_tasks', 'conversations', 'tasks',
            ['task_id'], ['id'], ondelete='CASCADE',
        )

    op.drop_constraint('uq_task_number_per_account', 'tasks', type_='unique')
    op.alter_column('tasks', 'account_id', nullable=True)
    op.drop_constraint('fk_tasks_ai_convo',     'tasks', type_='foreignkey')
    op.drop_constraint('fk_tasks_parent_convo', 'tasks', type_='foreignkey')
    op.drop_column('tasks', 'task_number')
    op.drop_column('tasks', 'ai_conversation_id')
    op.drop_column('tasks', 'parent_conversation_id')
