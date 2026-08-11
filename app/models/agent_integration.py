"""
AgentIntegration model for third-party AI coding agents (Claude Code, Codex, etc.)
posting into Forge channels and picking up assigned work.

Unlike APIToken (which authenticates as a full admin user), an AgentIntegration
is scoped to one workspace and optionally a specific set of channels, and
authenticates as itself — not as any human user. Messages it posts render
with external_source="agent" and no user_id, the same pattern used for
bridged Slack/Discord messages.

Two auth modes are supported on the same record:
- Bearer token (Authorization: Bearer <token>) for simple push-based agents.
- HMAC-signed webhook (X-Forge-Agent-Signature) using webhook_secret, for
  higher-trust CI-triggered integrations that don't want to hold a bearer
  token in a long-lived environment.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


def generate_agent_token() -> str:
    return "agt_" + secrets.token_hex(24)


def generate_webhook_secret() -> str:
    return secrets.token_hex(32)


class AgentIntegration(Base, TimestampMixin):
    """A registered third-party AI agent (Claude Code, Codex, custom bot)."""

    __tablename__ = "agent_integrations"

    id: Mapped[int] = mapped_column(primary_key=True)

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Admin who created/owns this integration
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Display identity, e.g. "Claude Code", "Codex", "Release Bot"
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="custom")  # "claude_code" | "codex" | "custom"
    avatar_emoji: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # Auth: bearer token (nullable if this integration only uses signed webhooks)
    token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    # Auth: HMAC secret (nullable if this integration only uses bearer tokens)
    webhook_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Scope: NULL means "all channels in this workspace"; otherwise a list of channel IDs
    allowed_channel_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Optional link back to a Labs project/product this agent works against
    buildly_product_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)

    workspace = relationship("Workspace")
    creator = relationship("User", foreign_keys=[created_by])

    @classmethod
    def create(
        cls,
        workspace_id: int,
        created_by: int,
        name: str,
        provider: str = "custom",
        with_token: bool = True,
        with_webhook_secret: bool = False,
        allowed_channel_ids: list[int] | None = None,
    ) -> "AgentIntegration":
        return cls(
            workspace_id=workspace_id,
            created_by=created_by,
            name=name.strip(),
            provider=provider,
            token=generate_agent_token() if with_token else None,
            webhook_secret=generate_webhook_secret() if with_webhook_secret else None,
            allowed_channel_ids=allowed_channel_ids,
        )

    @property
    def is_valid(self) -> bool:
        return self.is_active and self.revoked_at is None

    def can_access_channel(self, channel_id: int) -> bool:
        if self.allowed_channel_ids is None:
            return True
        return channel_id in self.allowed_channel_ids

    def verify_signature(self, body: bytes, timestamp: str, signature: str) -> bool:
        """Verify an X-Forge-Agent-Signature HMAC header.

        Signature is hex(HMAC-SHA256(webhook_secret, f"{timestamp}:{body}")).
        Callers should also reject stale timestamps (> 5 minutes old).
        """
        if not self.webhook_secret:
            return False
        payload = f"{timestamp}:".encode() + body
        expected = hmac.new(self.webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def revoke(self) -> None:
        self.is_active = False
        self.revoked_at = datetime.now(timezone.utc)

    def touch(self) -> None:
        self.last_used_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return f"<AgentIntegration {self.name!r} workspace={self.workspace_id} active={self.is_active}>"
