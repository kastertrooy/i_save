"""Add Instagram username to users

Revision ID: 010_user_instagram_username
Revises: 009_service_instance_started_at
Create Date: 2026-05-10 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


revision = '010_user_instagram_username'
down_revision = '009_service_instance_started_at'
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.add_column('users', sa.Column('instagram_username', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'instagram_username')
