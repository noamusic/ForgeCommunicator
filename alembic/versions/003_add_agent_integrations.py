"""Add agent_integrations table and agent-related columns.

Revision ID: 003
Revises: 002
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_integrations (
            id SERIAL PRIMARY KEY,
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(100) NOT NULL,
            provider VARCHAR(50) NOT NULL DEFAULT 'custom',
            avatar_emoji VARCHAR(8),
            token VARCHAR(64) UNIQUE,
            webhook_secret VARCHAR(64),
            allowed_channel_ids INTEGER[],
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            revoked_at TIMESTAMP WITH TIME ZONE,
            last_used_at TIMESTAMP WITH TIME ZONE,
            buildly_product_uuid VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_integrations_workspace_id ON agent_integrations (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_integrations_token ON agent_integrations (token)")

    op.execute("""
        ALTER TABLE messages
            ADD COLUMN IF NOT EXISTS agent_integration_id INTEGER REFERENCES agent_integrations(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS agent_message_kind VARCHAR(20)
    """)

    op.execute("""
        ALTER TABLE artifacts
            ADD COLUMN IF NOT EXISTS assignee_agent_id INTEGER REFERENCES agent_integrations(id) ON DELETE SET NULL
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE artifacts DROP COLUMN IF EXISTS assignee_agent_id")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS agent_message_kind")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS agent_integration_id")
    op.execute("DROP TABLE IF EXISTS agent_integrations CASCADE")
