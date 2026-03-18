"""Add all missing columns to match model definitions

Revision ID: 020_add_missing_columns
Revises: 019_fix_membership_timestamps
Create Date: 2026-03-18

This migration performs a comprehensive schema sync to add all columns
that exist in SQLAlchemy models but are missing from the database.

Uses inspect() to check if columns exist before adding them, making
this migration safe to run multiple times (idempotent).

Tables affected:
- workspaces: description, google_domain, google_auto_join, buildly_org_uuid,
              labs_api_token, labs_access_token, labs_refresh_token,
              labs_token_expires_at, labs_connected_by_id, invite_expires_at, icon_url
- users: phone, timezone, status, status_message, auth_provider, provider_sub,
         labs_access_token, labs_refresh_token, labs_token_expires_at,
         session_token, session_expires_at, is_platform_admin, last_seen_at
- channels: description, is_default, is_archived
- products: description, github_repo_url, github_org, icon_url, is_active
- channel_memberships: last_read_message_id, notify_all_messages
- messages: thread_reply_count, updated_at
- site_configs: entire table
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# Revision identifiers, used by Alembic.
revision: str = '020_add_missing_columns'
down_revision: Union[str, None] = '019_fix_membership_timestamps'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def table_exists(table_name: str) -> bool:
    """Check if a table exists."""
    conn = op.get_bind()
    inspector = inspect(conn)
    return table_name in inspector.get_table_names()


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def add_column_if_not_exists(table: str, column_name: str, column_type, **kwargs) -> None:
    """Add a column only if it doesn't already exist."""
    if not column_exists(table, column_name):
        op.add_column(table, sa.Column(column_name, column_type, **kwargs))


def upgrade() -> None:
    # ============================================================
    # WORKSPACES TABLE
    # ============================================================
    add_column_if_not_exists(
        'workspaces', 'description',
        sa.String(500), nullable=True
    )
    add_column_if_not_exists(
        'workspaces', 'google_domain',
        sa.String(255), nullable=True
    )
    add_column_if_not_exists(
        'workspaces', 'google_auto_join',
        sa.Boolean(), nullable=False, server_default='false'
    )
    add_column_if_not_exists(
        'workspaces', 'buildly_org_uuid',
        sa.String(36), nullable=True
    )
    add_column_if_not_exists(
        'workspaces', 'labs_api_token',
        sa.Text(), nullable=True
    )
    add_column_if_not_exists(
        'workspaces', 'labs_access_token',
        sa.Text(), nullable=True
    )
    add_column_if_not_exists(
        'workspaces', 'labs_refresh_token',
        sa.Text(), nullable=True
    )
    add_column_if_not_exists(
        'workspaces', 'labs_token_expires_at',
        sa.DateTime(timezone=True), nullable=True
    )
    add_column_if_not_exists(
        'workspaces', 'labs_connected_by_id',
        sa.Integer(), nullable=True
    )
    add_column_if_not_exists(
        'workspaces', 'invite_expires_at',
        sa.DateTime(timezone=True), nullable=True
    )
    add_column_if_not_exists(
        'workspaces', 'icon_url',
        sa.String(500), nullable=True
    )

    # ============================================================
    # USERS TABLE
    # ============================================================
    add_column_if_not_exists(
        'users', 'phone',
        sa.String(30), nullable=True
    )
    add_column_if_not_exists(
        'users', 'timezone',
        sa.String(50), nullable=True, server_default='UTC'
    )
    add_column_if_not_exists(
        'users', 'status',
        sa.String(20), nullable=False, server_default='active'
    )
    add_column_if_not_exists(
        'users', 'status_message',
        sa.String(100), nullable=True
    )
    add_column_if_not_exists(
        'users', 'auth_provider',
        sa.String(20), nullable=False, server_default='local'
    )
    add_column_if_not_exists(
        'users', 'provider_sub',
        sa.String(255), nullable=True
    )
    add_column_if_not_exists(
        'users', 'labs_access_token',
        sa.Text(), nullable=True
    )
    add_column_if_not_exists(
        'users', 'labs_refresh_token',
        sa.Text(), nullable=True
    )
    add_column_if_not_exists(
        'users', 'labs_token_expires_at',
        sa.DateTime(timezone=True), nullable=True
    )
    add_column_if_not_exists(
        'users', 'session_token',
        sa.String(64), nullable=True
    )
    add_column_if_not_exists(
        'users', 'session_expires_at',
        sa.DateTime(timezone=True), nullable=True
    )
    add_column_if_not_exists(
        'users', 'is_platform_admin',
        sa.Boolean(), nullable=False, server_default='false'
    )
    add_column_if_not_exists(
        'users', 'last_seen_at',
        sa.DateTime(timezone=True), nullable=True
    )
    
    # Handle password column name mismatch: model uses hashed_password, DB has password_hash
    # Add hashed_password if it doesn't exist (and copy data from password_hash if it exists)
    if not column_exists('users', 'hashed_password') and column_exists('users', 'password_hash'):
        op.alter_column('users', 'password_hash', new_column_name='hashed_password')
    elif not column_exists('users', 'hashed_password') and not column_exists('users', 'password_hash'):
        add_column_if_not_exists(
            'users', 'hashed_password',
            sa.String(255), nullable=True
        )
    
    # Create unique index on session_token if it doesn't exist
    conn = op.get_bind()
    inspector = inspect(conn)
    indexes = [idx['name'] for idx in inspector.get_indexes('users')]
    if 'ix_users_session_token' not in indexes:
        op.create_index('ix_users_session_token', 'users', ['session_token'], unique=True)

    # ============================================================
    # CHANNELS TABLE
    # ============================================================
    add_column_if_not_exists(
        'channels', 'description',
        sa.Text(), nullable=True
    )
    add_column_if_not_exists(
        'channels', 'is_default',
        sa.Boolean(), nullable=False, server_default='false'
    )
    add_column_if_not_exists(
        'channels', 'is_archived',
        sa.Boolean(), nullable=False, server_default='false'
    )

    # ============================================================
    # PRODUCTS TABLE
    # ============================================================
    add_column_if_not_exists(
        'products', 'description',
        sa.Text(), nullable=True
    )
    add_column_if_not_exists(
        'products', 'github_repo_url',
        sa.String(500), nullable=True
    )
    add_column_if_not_exists(
        'products', 'github_org',
        sa.String(100), nullable=True
    )
    add_column_if_not_exists(
        'products', 'icon_url',
        sa.String(500), nullable=True
    )
    add_column_if_not_exists(
        'products', 'is_active',
        sa.Boolean(), nullable=False, server_default='true'
    )
    
    # Handle buildly_product_uuid vs buildly_product_id mismatch
    if not column_exists('products', 'buildly_product_uuid') and column_exists('products', 'buildly_product_id'):
        op.alter_column('products', 'buildly_product_id', new_column_name='buildly_product_uuid')
    elif not column_exists('products', 'buildly_product_uuid') and not column_exists('products', 'buildly_product_id'):
        add_column_if_not_exists(
            'products', 'buildly_product_uuid',
            sa.String(36), nullable=True
        )

    # ============================================================
    # CHANNEL_MEMBERSHIPS TABLE
    # ============================================================
    add_column_if_not_exists(
        'channel_memberships', 'last_read_message_id',
        sa.Integer(), nullable=True
    )
    add_column_if_not_exists(
        'channel_memberships', 'notify_all_messages',
        sa.Boolean(), nullable=False, server_default='false'
    )
    
    # ============================================================
    # MEMBERSHIPS TABLE (workspace memberships)
    # ============================================================
    add_column_if_not_exists(
        'memberships', 'notifications_enabled',
        sa.Boolean(), nullable=False, server_default='true'
    )
    add_column_if_not_exists(
        'memberships', 'notify_all_messages',
        sa.Boolean(), nullable=False, server_default='false'
    )

    # ============================================================
    # MESSAGES TABLE
    # ============================================================
    add_column_if_not_exists(
        'messages', 'thread_reply_count',
        sa.Integer(), nullable=False, server_default='0'
    )
    add_column_if_not_exists(
        'messages', 'updated_at',
        sa.DateTime(timezone=True), nullable=True
    )
    
    # Handle user_id vs author_id mismatch - model uses user_id, DB has author_id
    if not column_exists('messages', 'user_id') and column_exists('messages', 'author_id'):
        op.alter_column('messages', 'author_id', new_column_name='user_id')
    elif not column_exists('messages', 'user_id'):
        add_column_if_not_exists(
            'messages', 'user_id',
            sa.Integer(), nullable=True
        )
    
    # Handle parent_id vs thread_parent_id mismatch - model uses parent_id
    if not column_exists('messages', 'parent_id') and column_exists('messages', 'thread_parent_id'):
        op.alter_column('messages', 'thread_parent_id', new_column_name='parent_id')
    elif not column_exists('messages', 'parent_id'):
        add_column_if_not_exists(
            'messages', 'parent_id',
            sa.Integer(), nullable=True
        )
    
    # Handle deleted_at (DateTime) vs is_deleted (Boolean) - model uses deleted_at
    add_column_if_not_exists(
        'messages', 'deleted_at',
        sa.DateTime(timezone=True), nullable=True
    )
    
    # Add external source columns for Slack/Discord bridging
    add_column_if_not_exists(
        'messages', 'external_source',
        sa.String(20), nullable=True
    )
    add_column_if_not_exists(
        'messages', 'external_message_id',
        sa.String(255), nullable=True
    )
    add_column_if_not_exists(
        'messages', 'external_channel_id',
        sa.String(255), nullable=True
    )
    add_column_if_not_exists(
        'messages', 'external_thread_ts',
        sa.String(255), nullable=True
    )
    add_column_if_not_exists(
        'messages', 'external_author_name',
        sa.String(255), nullable=True
    )
    add_column_if_not_exists(
        'messages', 'external_author_avatar',
        sa.Text(), nullable=True
    )

    # ============================================================
    # ARTIFACTS TABLE - Handle column name mismatches
    # ============================================================
    # workspace_id is required in model but wasn't in original migration
    add_column_if_not_exists(
        'artifacts', 'workspace_id',
        sa.Integer(), nullable=True  # Start nullable, we'll back-fill
    )
    add_column_if_not_exists(
        'artifacts', 'product_id',
        sa.Integer(), nullable=True
    )
    add_column_if_not_exists(
        'artifacts', 'severity',
        sa.String(20), nullable=True
    )
    add_column_if_not_exists(
        'artifacts', 'priority',
        sa.String(20), nullable=True
    )
    # source_message_id vs message_id
    if not column_exists('artifacts', 'source_message_id') and column_exists('artifacts', 'message_id'):
        op.alter_column('artifacts', 'message_id', new_column_name='source_message_id')
    elif not column_exists('artifacts', 'source_message_id'):
        add_column_if_not_exists(
            'artifacts', 'source_message_id',
            sa.Integer(), nullable=True
        )
    
    # Handle created_by vs author_id - model uses created_by
    if not column_exists('artifacts', 'created_by') and column_exists('artifacts', 'author_id'):
        op.alter_column('artifacts', 'author_id', new_column_name='created_by')
    elif not column_exists('artifacts', 'created_by'):
        add_column_if_not_exists(
            'artifacts', 'created_by',
            sa.Integer(), nullable=True
        )
    
    # Handle buildly_item_uuid vs buildly_artifact_id
    if not column_exists('artifacts', 'buildly_item_uuid') and column_exists('artifacts', 'buildly_artifact_id'):
        op.alter_column('artifacts', 'buildly_artifact_id', new_column_name='buildly_item_uuid')
    elif not column_exists('artifacts', 'buildly_item_uuid'):
        add_column_if_not_exists(
            'artifacts', 'buildly_item_uuid',
            sa.String(36), nullable=True
        )
    
    # Handle assignee_user_id vs assignee_id
    if not column_exists('artifacts', 'assignee_user_id') and column_exists('artifacts', 'assignee_id'):
        op.alter_column('artifacts', 'assignee_id', new_column_name='assignee_user_id')
    elif not column_exists('artifacts', 'assignee_user_id'):
        add_column_if_not_exists(
            'artifacts', 'assignee_user_id',
            sa.Integer(), nullable=True
        )

    # ============================================================
    # SITE_CONFIGS TABLE - Create if not exists
    # ============================================================
    if not table_exists('site_configs'):
        op.create_table(
            'site_configs',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('key', sa.String(100), nullable=False, unique=True, index=True),
            sa.Column('value', sa.Text(), nullable=True),
            sa.Column('json_value', sa.JSON(), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('updated_by', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    # ============================================================
    # NOTE_SHARES TABLE - Add missing message column
    # ============================================================
    if table_exists('note_shares'):
        add_column_if_not_exists(
            'note_shares', 'message',
            sa.String(500), nullable=True
        )


def downgrade() -> None:
    # Note: Downgrade is complex due to conditional column additions
    # This is intentionally minimal - we don't drop columns on downgrade
    # to preserve data safety
    
    # Drop site_configs table if it exists
    if table_exists('site_configs'):
        op.drop_table('site_configs')
    
    # Note: Column renames and additions are NOT reversed
    # to avoid data loss. If you need to truly downgrade,
    # restore from backup.
