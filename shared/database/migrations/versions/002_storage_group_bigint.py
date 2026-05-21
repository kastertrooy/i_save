"""Use BIGINT for Telegram storage group IDs

Revision ID: 002_storage_group_bigint
Revises: 001_initial
Create Date: 2026-05-09 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


revision = '002_storage_group_bigint'
down_revision = '001_initial'
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.alter_column(
        'telegram_storage_groups',
        'telegram_group_id',
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    op.create_unique_constraint(
        'uq_telegram_storage_groups_telegram_group_id',
        'telegram_storage_groups',
        ['telegram_group_id'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_telegram_storage_groups_telegram_group_id',
        'telegram_storage_groups',
        type_='unique',
    )
    op.alter_column(
        'telegram_storage_groups',
        'telegram_group_id',
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
