"""Store multiple Telegram file IDs for albums

Revision ID: 005_media_cache_file_ids
Revises: 004_delivery_logs_nullable_queue
Create Date: 2026-05-09 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


revision = '005_media_cache_file_ids'
down_revision = '004_delivery_logs_nullable_queue'
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.add_column('media_cache', sa.Column('telegram_file_ids', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('media_cache', 'telegram_file_ids')
