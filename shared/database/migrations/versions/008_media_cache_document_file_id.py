"""Add Telegram document file ID to media cache

Revision ID: 008_media_cache_document_file_id
Revises: 007_media_cache_size_created_at
Create Date: 2026-05-09 00:00:00.000000
"""

from alembic import op


revision = '008_media_cache_document_file_id'
down_revision = '007_media_cache_size_created_at'
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.execute('ALTER TABLE media_cache ADD COLUMN IF NOT EXISTS telegram_file_id_document VARCHAR')


def downgrade() -> None:
    op.execute('ALTER TABLE media_cache DROP COLUMN IF EXISTS telegram_file_id_document')
