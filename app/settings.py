"""
Application settings using Pydantic BaseSettings.
All secrets and configuration via environment variables.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "Forge Communicator"
    app_version: str = "0.3.0"  # Add message reactions and mark-as-artifact
    debug: bool = False
    secret_key: str = Field(default="change-me-in-production-use-openssl-rand-hex-32")
    
    # Registration mode: 'open' (anyone), 'invite_only' (workspace invite required), 'closed' (no new users)
    registration_mode: Literal["open", "invite_only", "closed"] = "open"
    
    # Platform admin emails (comma-separated) - these users get admin access
    platform_admin_emails: str = ""  # e.g. "admin@example.com,support@example.com"
    
    # White-label branding - customize per customer deployment
    brand_name: str = "Communicator"  # Display name in UI
    brand_logo_url: str | None = None  # URL to logo image (uses default if not set)
    brand_favicon_url: str | None = None  # URL to favicon (uses default if not set)
    brand_company: str = "Buildly"  # Company name for footer/copyright
    brand_support_email: str = "support@buildly.io"
    brand_primary_color: str = "#3b82f6"  # Blue-500 - main accent color (matches splash)
    brand_secondary_color: str = "#0f172a"  # Slate-900 - dark navy (matches splash)
    brand_accent_color: str = "#a855f7"  # Purple-500 - highlight color (matches splash)
    
    # Server
    host: str = "0.0.0.0"
    port: int = Field(default=8000, alias="PORT")
    
    # Build info (set by CI/CD)
    build_sha: str = Field(default="dev", alias="BUILD_SHA")
    
    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://forge:forge@localhost:5432/forge_communicator",
        alias="DATABASE_URL",
    )
    database_pool_size: int = 5
    database_max_overflow: int = 10
    
    @field_validator("database_url", mode="before")
    @classmethod
    def transform_database_url(cls, v: str) -> str:
        """Transform database URL to use asyncpg driver.
        
        Many deployment platforms provide postgres:// URLs that need
        to be converted to postgresql+asyncpg:// for SQLAlchemy async.
        Also removes sslmode parameter since asyncpg handles SSL differently.
        """
        import os
        import sys
        from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
        
        # Debug: Log all database-related env vars
        print(f"DATABASE_URL from env: {os.environ.get('DATABASE_URL', 'NOT SET')[:60] if os.environ.get('DATABASE_URL') else 'NOT SET'}", file=sys.stderr)
        print(f"Validator received value: {repr(v)[:60] if v else 'None/Empty'}", file=sys.stderr)
        
        if not v:
            print("DATABASE_URL is empty, using default", file=sys.stderr)
            return "postgresql+asyncpg://forge:forge@localhost:5432/forge_communicator"
        
        # Strip whitespace
        v = v.strip()
        
        # Handle ${...} variable substitution syntax (not yet resolved)
        if v.startswith("${") or not v:
            print(f"DATABASE_URL appears unresolved: {v[:40]}", file=sys.stderr)
            return "postgresql+asyncpg://forge:forge@localhost:5432/forge_communicator"
        
        # Log what we got for debugging
        print(f"DATABASE_URL processing: {v[:60]}...", file=sys.stderr)
        
        # Handle postgres:// -> postgresql+asyncpg://
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        # Handle postgresql:// without driver -> postgresql+asyncpg://
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        
        # Remove sslmode from query string (asyncpg doesn't support it as URL param)
        # We handle SSL via connect_args instead
        try:
            parsed = urlparse(v)
            if parsed.query:
                query_params = parse_qs(parsed.query)
                # Remove sslmode - we'll handle it in connect_args
                query_params.pop("sslmode", None)
                # Rebuild URL without sslmode
                new_query = urlencode(query_params, doseq=True)
                v = urlunparse((
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    new_query,
                    parsed.fragment,
                ))
        except Exception as e:
            print(f"Warning: Could not parse DATABASE_URL query params: {e}", file=sys.stderr)
        
        print(f"DATABASE_URL final: {v[:60]}...", file=sys.stderr)
        return v
    
    # For sync operations (Alembic)
    @computed_field
    @property
    def database_url_sync(self) -> str:
        """Convert async URL to sync for Alembic migrations."""
        return self.database_url.replace("+asyncpg", "").replace("+aiopg", "")
    
    # Realtime mode
    realtime_mode: Literal["ws", "poll"] = "ws"
    poll_interval_seconds: int = 3
    
    # Auth - Local
    password_min_length: int = 8
    session_expire_hours: int = 168  # 7 days default for browser
    session_expire_hours_pwa: int = 720  # 30 days for PWA mode (iOS Safari clears cookies aggressively)
    
    # Auth - Google OAuth (optional, per-deployment)
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None
    google_allowed_domain: str | None = None  # Restrict to this Google Workspace domain
    
    @field_validator("google_allowed_domain", mode="before")
    @classmethod
    def empty_string_to_none(cls, v: str | None) -> str | None:
        """Convert empty string to None for optional domain restriction."""
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        return v.strip()
    
    # Auth - Buildly Labs OAuth (first-party integration - hardcoded for all deployments)
    # Only the secret is configured via env var for security
    buildly_client_id: str = "forge-communicator"  # Registered in Labs
    buildly_client_secret: str | None = None  # Set via BUILDLY_CLIENT_SECRET env var
    buildly_redirect_uri: str = "https://comms.buildly.io/auth/oauth/buildly/callback"
    buildly_oauth_url: str = "https://labs.buildly.io"  # OAuth endpoints
    buildly_api_url: str = "https://labs.buildly.io/api/v1"  # User info API
    
    # Buildly Labs API (for syncing)
    labs_api_key: str | None = None  # Optional - falls back to user's OAuth token
    labs_api_url: str = "https://labs.buildly.io/api/v1"
    
    # Buildly CollabHub API (for community/profile sync)
    # CollabHub shares Labs identity but has separate profile and community data
    # This is a plugin that can be enabled/disabled per deployment
    collabhub_enabled: bool = False  # Master switch for CollabHub integration
    collabhub_community_workspace_enabled: bool = False  # Auto-join users to Community workspace
    collabhub_api_url: str = "https://collab.buildly.io/api"
    collabhub_api_key: str | None = None  # Optional API key for service-to-service auth
    
    # Rate limiting
    rate_limit_auth_per_minute: int = 10
    rate_limit_api_per_minute: int = 60
    
    # Push notifications (VAPID)
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_contact_email: str = "admin@buildly.io"
    
    # CORS (for API access if needed)
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8000"]
    
    # Email configuration (SMTP)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_from_email: str | None = None  # Defaults to brand_support_email
    smtp_from_name: str | None = None  # Defaults to brand_name

    @field_validator("smtp_port", mode="before")
    @classmethod
    def empty_str_to_default_port(cls, v):
        if v == "" or v is None:
            return 587
        return v

    @field_validator("smtp_use_tls", mode="before")
    @classmethod
    def empty_str_to_default_tls(cls, v):
        if v == "" or v is None:
            return True
        return v
    
    # Email configuration (API providers - alternative to SMTP)
    sendgrid_api_key: str | None = None
    mailgun_api_key: str | None = None
    mailgun_domain: str | None = None
    
    # Slack integration (for receiving notifications)
    slack_client_id: str | None = None
    slack_client_secret: str | None = None
    slack_signing_secret: str | None = None  # For verifying webhook requests
    
    # Discord integration (for receiving notifications)
    discord_client_id: str | None = None
    discord_client_secret: str | None = None
    discord_bot_token: str | None = None  # Optional bot token for extended features
    
    # Error reporting - GitHub Issues
    github_error_repo: str | None = None  # e.g. "owner/repo"
    github_error_token: str | None = None  # GitHub PAT with repo access
    github_error_max_comments: int = 3  # After this, reactions are used
    
    # Error reporting - Labs Punchlist (syncs with GitHub issues)
    labs_error_product_uuid: str | None = None  # Labs product UUID for error tracking
    # Note: labs_api_url and labs_api_key are already defined above
    
    # File storage (S3-compatible - works with AWS S3 or DigitalOcean Spaces)
    # For DigitalOcean Spaces: endpoint is https://<region>.digitaloceanspaces.com
    storage_endpoint: str | None = None  # e.g. "https://nyc3.digitaloceanspaces.com"
    storage_access_key: str | None = None  # Access key ID
    storage_secret_key: str | None = None  # Secret access key
    storage_bucket: str | None = None  # Bucket/Space name
    storage_region: str = "nyc3"  # Region for S3/Spaces
    storage_public_url: str | None = None  # CDN or public URL prefix (optional)
    
    # Upload limits
    upload_max_size_mb: int = 25  # Maximum file size in MB
    upload_allowed_types: str = "image/*,application/pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.csv,.json,.xml,.zip,.tar,.gz"
    
    # Base URL for links in emails
    base_url: str = "http://localhost:8000"
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # json or text
    
    @property
    def google_oauth_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)
    
    @property
    def buildly_oauth_enabled(self) -> bool:
        # Only need the secret - client_id is hardcoded
        return bool(self.buildly_client_secret)
    
    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key)
    
    @property
    def slack_enabled(self) -> bool:
        """Check if Slack integration is configured."""
        return bool(self.slack_client_id and self.slack_client_secret)
    
    @property
    def discord_enabled(self) -> bool:
        """Check if Discord integration is configured."""
        return bool(self.discord_client_id and self.discord_client_secret)
    
    @property
    def email_configured(self) -> bool:
        """Check if any email provider is configured."""
        return bool(
            self.smtp_host or
            self.sendgrid_api_key or
            (self.mailgun_api_key and self.mailgun_domain)
        )
    
    @property
    def github_error_reporting_enabled(self) -> bool:
        """Check if GitHub error reporting is configured."""
        return bool(self.github_error_repo and self.github_error_token)
    
    @property
    def labs_error_reporting_enabled(self) -> bool:
        """Check if Labs error reporting is configured."""
        return bool(self.labs_error_product_uuid and self.labs_api_key)
    
    @property
    def file_storage_enabled(self) -> bool:
        """Check if file storage (S3/Spaces) is configured."""
        return bool(
            self.storage_endpoint and
            self.storage_access_key and
            self.storage_secret_key and
            self.storage_bucket
        )
    
    @property
    def upload_max_size_bytes(self) -> int:
        """Get max upload size in bytes."""
        return self.upload_max_size_mb * 1024 * 1024
    
    @property
    def admin_emails_list(self) -> list[str]:
        """Parse platform admin emails into a list."""
        if not self.platform_admin_emails:
            return []
        return [e.strip().lower() for e in self.platform_admin_emails.split(",") if e.strip()]
    
    def is_admin_email(self, email: str) -> bool:
        """Check if email is a platform admin."""
        return email.lower() in self.admin_emails_list


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
