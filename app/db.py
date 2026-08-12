"""
Database configuration with SQLAlchemy 2.0 async support.
"""

import asyncio
import ssl
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.settings import settings


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


def _get_connect_args() -> dict:
    """Get connection arguments, including SSL for managed databases."""
    import sys
    
    connect_args = {}
    
    # DigitalOcean and other managed databases require SSL
    # Skip SSL for local development (localhost, 127.0.0.1, or Docker service names)
    db_url = settings.database_url
    local_hosts = ["localhost", "127.0.0.1", "@db:", "@db/", "@postgres:", "@postgres/"]
    is_local = any(host in db_url for host in local_hosts)
    
    # Check for known managed database hosts that require SSL
    managed_db_hosts = [".db.ondigitalocean.com", ".rds.amazonaws.com", ".cloud.google.com"]
    is_managed = any(host in db_url for host in managed_db_hosts)
    
    if is_managed or not is_local:
        # Create SSL context for managed databases
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE  # Managed DBs often use self-signed certs
        connect_args["ssl"] = ssl_context
        print(f"SSL enabled for database connection", file=sys.stderr)
    else:
        print(f"SSL disabled (local database)", file=sys.stderr)
    
    return connect_args


# Create async engine
engine = create_async_engine(
    settings.database_url,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    echo=settings.debug,
    connect_args=_get_connect_args(),
    pool_pre_ping=True,  # Verify connections before using
)

# Session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides a database session."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for database sessions (for use outside of FastAPI)."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Initialize database (create tables if needed) with retry logic."""
    import sys
    from urllib.parse import urlparse
    from sqlalchemy import text
    
    # More retries for managed databases that may take time to be ready
    max_retries = 10
    retry_delay = 5  # seconds
    
    # Log which database we're connecting to (mask password)
    db_url = settings.database_url
    try:
        parsed = urlparse(db_url)
        masked_url = f"{parsed.scheme}://{parsed.username}:***@{parsed.hostname}:{parsed.port}{parsed.path}"
        print(f"Connecting to database: {masked_url}", file=sys.stderr)
    except Exception:
        print("Connecting to database...", file=sys.stderr)
    
    for attempt in range(max_retries):
        try:
            async with engine.begin() as conn:
                # Import all models to ensure they're registered
                from app.models import (  # noqa: F401
                    agent_integration,
                    api_token,
                    artifact,
                    channel,
                    membership,
                    message,
                    product,
                    push_subscription,
                    site_config,
                    user,
                    workspace,
                )
                await conn.run_sync(Base.metadata.create_all)
                print(f"Database tables initialized", file=sys.stderr)
                
                # Run safe migrations for columns that may be missing
                # These use IF NOT EXISTS so they're safe to run multiple times
                migrations = [
                    # Labs SSO columns (added 2026-01-19)
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS labs_user_id INTEGER",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS labs_org_uuid VARCHAR(36)",
                    # Platform admin column (added 2026-01-19)
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_platform_admin BOOLEAN DEFAULT FALSE",
                    # Labs OAuth token columns (added 2026-01-19)
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS labs_access_token TEXT",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS labs_refresh_token TEXT",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS labs_token_expires_at TIMESTAMP WITH TIME ZONE",
                    # Workspace-level Labs integration columns (added 2026-01-19)
                    "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS labs_api_token TEXT",
                    "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS labs_access_token TEXT",
                    "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS labs_refresh_token TEXT",
                    "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS labs_token_expires_at TIMESTAMP WITH TIME ZONE",
                    "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS labs_connected_by_id INTEGER",
                    # User session columns (added 2026-02-01) - ensure session management works
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS session_token VARCHAR(64) UNIQUE",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS session_expires_at TIMESTAMP WITH TIME ZONE",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITH TIME ZONE",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(20) DEFAULT 'local' NOT NULL",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS provider_sub VARCHAR(255)",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS hashed_password VARCHAR(255)",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(30)",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'UTC'",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active' NOT NULL",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS status_message VARCHAR(100)",
                    "CREATE INDEX IF NOT EXISTS ix_users_session_token ON users(session_token)",
                    # Site config table (added 2026-01-20)
                    """CREATE TABLE IF NOT EXISTS site_configs (
                        id SERIAL PRIMARY KEY,
                        key VARCHAR(100) UNIQUE NOT NULL,
                        value TEXT,
                        json_value JSONB,
                        description TEXT,
                        updated_by INTEGER,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )""",
                    "CREATE INDEX IF NOT EXISTS ix_site_configs_key ON site_configs(key)",
                    # Membership notification preferences (added 2026-02-25)
                    "ALTER TABLE memberships ADD COLUMN IF NOT EXISTS notify_all_messages BOOLEAN NOT NULL DEFAULT FALSE",
                    # API tokens table (added 2026-04-14)
                    # Fix: drop broken table if it exists without the token column
                    # (caused by create_all running with incomplete metadata on a prior deploy)
                    """DO $$
                    BEGIN
                        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'api_tokens')
                           AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'api_tokens' AND column_name = 'token')
                        THEN
                            DROP TABLE api_tokens CASCADE;
                        END IF;
                    END $$""",
                    """CREATE TABLE IF NOT EXISTS api_tokens (
                        id SERIAL PRIMARY KEY,
                        token VARCHAR(64) UNIQUE NOT NULL,
                        name VARCHAR(100) NOT NULL,
                        description TEXT,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        expires_at TIMESTAMP WITH TIME ZONE,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        revoked_at TIMESTAMP WITH TIME ZONE,
                        last_used_at TIMESTAMP WITH TIME ZONE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
                    )""",
                    "CREATE INDEX IF NOT EXISTS ix_api_tokens_token ON api_tokens(token)",
                    "CREATE INDEX IF NOT EXISTS ix_api_tokens_user_id ON api_tokens(user_id)",
                    # Agent integrations for third-party AI coding agents (added 2026-08-07)
                    """CREATE TABLE IF NOT EXISTS agent_integrations (
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
                    )""",
                    "CREATE INDEX IF NOT EXISTS ix_agent_integrations_workspace_id ON agent_integrations(workspace_id)",
                    "CREATE INDEX IF NOT EXISTS ix_agent_integrations_token ON agent_integrations(token)",
                    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS agent_integration_id INTEGER REFERENCES agent_integrations(id) ON DELETE SET NULL",
                    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS agent_message_kind VARCHAR(20)",
                    "ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS assignee_agent_id INTEGER REFERENCES agent_integrations(id) ON DELETE SET NULL",
                ]
                
                for migration in migrations:
                    try:
                        await conn.execute(text(migration))
                        print(f"Migration OK: {migration[:50]}...", file=sys.stderr)
                    except Exception as e:
                        # Log but don't fail - column might already exist or syntax differs
                        print(f"Migration skipped: {e}", file=sys.stderr)
                
                print(f"Database initialized successfully", file=sys.stderr)
                return
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Database connection attempt {attempt + 1}/{max_retries} failed: {e}", file=sys.stderr)
                print(f"Retrying in {retry_delay} seconds...", file=sys.stderr)
                await asyncio.sleep(retry_delay)
            else:
                print(f"Database connection failed after {max_retries} attempts", file=sys.stderr)
                raise


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()
