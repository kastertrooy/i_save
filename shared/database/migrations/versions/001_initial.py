"""Initial database schema

Revision ID: 001_initial
Revises: 
Create Date: 2026-05-06 00:00:00.000000
"""

from alembic import op
from sqlalchemy import engine_from_config

from shared.database.models import Base

# revision identifiers, used by Alembic.
revision = '001_initial'
down_revision = None
branch_labels = None
dependencies = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
