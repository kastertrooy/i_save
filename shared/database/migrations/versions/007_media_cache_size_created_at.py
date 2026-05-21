"""Add size and created_at to media cache

Revision ID: 007_media_cache_size_created_at
Revises: 006_delivery_logs_created_at
Create Date: 2026-05-09 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


revision = '007_media_cache_size_created_at'
down_revision = '006_delivery_logs_created_at'
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.add_column(
        'media_cache',
        sa.Column('size_mb', sa.Float(), nullable=False, server_default='0'),
    )
    op.add_column(
        'media_cache',
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.alter_column('media_cache', 'size_mb', server_default=None)
    op.alter_column('media_cache', 'created_at', server_default=None)


def downgrade() -> None:
    op.drop_column('media_cache', 'created_at')
    op.drop_column('media_cache', 'size_mb')
