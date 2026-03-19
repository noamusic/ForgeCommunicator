"""Fix membership timestamp columns

Revision ID: 019
Revises: 018
Create Date: 2026-03-18

The memberships and channel_memberships tables were created with 'joined_at'
but the models now expect 'created_at' and 'updated_at' from TimestampMixin.
This migration adds the missing timestamp columns.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Fix memberships table
    membership_columns = [col['name'] for col in inspector.get_columns('memberships')]
    
    # Add created_at if missing (copy from joined_at if it exists)
    if 'created_at' not in membership_columns:
        if 'joined_at' in membership_columns:
            # Rename joined_at to created_at
            op.alter_column('memberships', 'joined_at', new_column_name='created_at')
        else:
            # Add new created_at column
            op.add_column(
                'memberships',
                sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
            )
    
    # Re-check columns after potential rename
    membership_columns = [col['name'] for col in inspector.get_columns('memberships')]
    
    # Add updated_at if missing
    if 'updated_at' not in membership_columns:
        op.add_column(
            'memberships',
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
        )
    
    # Fix channel_memberships table
    channel_membership_columns = [col['name'] for col in inspector.get_columns('channel_memberships')]
    
    # Add created_at if missing (copy from joined_at if it exists)
    if 'created_at' not in channel_membership_columns:
        if 'joined_at' in channel_membership_columns:
            # Rename joined_at to created_at
            op.alter_column('channel_memberships', 'joined_at', new_column_name='created_at')
        else:
            # Add new created_at column
            op.add_column(
                'channel_memberships',
                sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
            )
    
    # Re-check columns after potential rename
    channel_membership_columns = [col['name'] for col in inspector.get_columns('channel_memberships')]
    
    # Add updated_at if missing
    if 'updated_at' not in channel_membership_columns:
        op.add_column(
            'channel_memberships',
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Revert channel_memberships
    channel_membership_columns = [col['name'] for col in inspector.get_columns('channel_memberships')]
    
    if 'updated_at' in channel_membership_columns:
        op.drop_column('channel_memberships', 'updated_at')
    
    if 'created_at' in channel_membership_columns:
        op.alter_column('channel_memberships', 'created_at', new_column_name='joined_at')
    
    # Revert memberships
    membership_columns = [col['name'] for col in inspector.get_columns('memberships')]
    
    if 'updated_at' in membership_columns:
        op.drop_column('memberships', 'updated_at')
    
    if 'created_at' in membership_columns:
        op.alter_column('memberships', 'created_at', new_column_name='joined_at')
