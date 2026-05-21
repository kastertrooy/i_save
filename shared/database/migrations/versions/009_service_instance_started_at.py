"""Add service instance started_at

Revision ID: 009_service_instance_started_at
Revises: 008_media_cache_document_file_id
Create Date: 2026-05-09 00:00:00.000000
"""

from alembic import op


revision = '009_service_instance_started_at'
down_revision = '008_media_cache_document_file_id'
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.execute('ALTER TABLE service_instances ADD COLUMN IF NOT EXISTS started_at TIMESTAMP')
    op.execute(
        'UPDATE service_instances '
        'SET started_at = COALESCE(last_heartbeat_at, NOW()) '
        'WHERE started_at IS NULL'
    )


def downgrade() -> None:
    op.execute('ALTER TABLE service_instances DROP COLUMN IF EXISTS started_at')
