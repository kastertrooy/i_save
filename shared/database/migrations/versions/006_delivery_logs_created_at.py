"""Add created_at to delivery logs

Revision ID: 006_delivery_logs_created_at
Revises: 005_media_cache_file_ids
Create Date: 2026-05-09 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


revision = '006_delivery_logs_created_at'
down_revision = '005_media_cache_file_ids'
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.add_column(
        'delivery_logs',
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.alter_column('delivery_logs', 'created_at', server_default=None)


def downgrade() -> None:
    op.drop_column('delivery_logs', 'created_at')
