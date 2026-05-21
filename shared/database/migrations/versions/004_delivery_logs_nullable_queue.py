"""Allow deleting delivered content queue rows

Revision ID: 004_delivery_logs_nullable_queue
Revises: 003_user_telegram_chat_id_bigint
Create Date: 2026-05-09 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


revision = '004_delivery_logs_nullable_queue'
down_revision = '003_user_telegram_chat_id_bigint'
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.drop_constraint('delivery_logs_content_queue_id_fkey', 'delivery_logs', type_='foreignkey')
    op.alter_column(
        'delivery_logs',
        'content_queue_id',
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_foreign_key(
        'delivery_logs_content_queue_id_fkey',
        'delivery_logs',
        'content_queue',
        ['content_queue_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('delivery_logs_content_queue_id_fkey', 'delivery_logs', type_='foreignkey')
    op.alter_column(
        'delivery_logs',
        'content_queue_id',
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        'delivery_logs_content_queue_id_fkey',
        'delivery_logs',
        'content_queue',
        ['content_queue_id'],
        ['id'],
    )
