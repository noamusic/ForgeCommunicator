"""Add account approval fields to users table.

Revision ID: 024
Revises: 023_add_thread_read_states
Create Date: 2026-03-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '024_add_account_approval'
down_revision = '023_add_thread_read_states'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add account approval columns to users table."""
    # Add is_approved column - default True for existing users
    op.add_column(
        'users',
        sa.Column('is_approved', sa.Boolean(), nullable=False, server_default='true')
    )
    
    # Add approved_at timestamp
    op.add_column(
        'users',
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True)
    )
    
    # Add approved_by_id foreign key
    op.add_column(
        'users',
        sa.Column('approved_by_id', sa.Integer(), nullable=True)
    )
    
    # Add foreign key constraint
    op.create_foreign_key(
        'fk_users_approved_by',
        'users',
        'users',
        ['approved_by_id'],
        ['id'],
        ondelete='SET NULL'
    )
    
    # Add can_create_workspaces column - default True for existing users
    op.add_column(
        'users',
        sa.Column('can_create_workspaces', sa.Boolean(), nullable=False, server_default='true')
    )
    
    # Mark all existing users as approved
    op.execute("UPDATE users SET is_approved = true, can_create_workspaces = true")


def downgrade() -> None:
    """Remove account approval columns."""
    op.drop_constraint('fk_users_approved_by', 'users', type_='foreignkey')
    op.drop_column('users', 'can_create_workspaces')
    op.drop_column('users', 'approved_by_id')
    op.drop_column('users', 'approved_at')
    op.drop_column('users', 'is_approved')
