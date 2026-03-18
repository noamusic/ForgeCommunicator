"""Drop legacy display_name column from channels

Revision ID: 021_drop_channels_display_name
Revises: 020_add_missing_columns
Create Date: 2026-03-18

The channels table was created with a display_name column, but the model
now computes display_name as a @property (prepending # to the name).
This column is unused and causes NOT NULL constraint violations.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = '021_drop_channels_display_name'
down_revision: Union[str, None] = '020_add_missing_columns'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    # Drop display_name column if it exists (it's now a computed property)
    if column_exists('channels', 'display_name'):
        op.drop_column('channels', 'display_name')
    
    # Also drop created_by_id if it exists (not in current model)
    if column_exists('channels', 'created_by_id'):
        op.drop_column('channels', 'created_by_id')


def downgrade() -> None:
    # Re-add display_name column
    if not column_exists('channels', 'display_name'):
        op.add_column(
            'channels',
            sa.Column('display_name', sa.String(100), nullable=True)
        )
