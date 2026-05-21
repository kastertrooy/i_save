"""Use BIGINT for Telegram user chat IDs

Revision ID: 003_user_telegram_chat_id_bigint
Revises: 002_storage_group_bigint
Create Date: 2026-05-09 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


revision = '003_user_telegram_chat_id_bigint'
down_revision = '002_storage_group_bigint'
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.alter_column(
        'users',
        'telegram_chat_id',
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        'users',
        'telegram_chat_id',
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
