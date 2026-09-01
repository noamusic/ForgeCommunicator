"""
Message model for chat messages.
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class ExternalSource(str, Enum):
    """External platforms that messages can originate from."""
    SLACK = "slack"
    DISCORD = "discord"
    AGENT = "agent"  # Third-party AI coding agent (Claude Code, Codex, etc.)


class Message(Base, TimestampMixin):
    """Message model for chat."""
    
    __tablename__ = "messages"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)  # Nullable for external messages
    
    # Content (body is the canonical field, content is an alias)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Threading (optional, phase 2)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    thread_reply_count: Mapped[int] = mapped_column(default=0, nullable=False)
    
    # Edit/delete tracking
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # External source tracking (for bridged Slack/Discord messages)
    external_source: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)  # 'slack', 'discord', or None
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Original message ID
    external_channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Original channel ID
    external_thread_ts: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Slack thread timestamp
    external_author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Original author display name
    external_author_avatar: Mapped[str | None] = mapped_column(Text, nullable=True)  # Original author avatar URL

    # Set when external_source == "agent": which AgentIntegration posted this message.
    agent_integration_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_integrations.id", ondelete="SET NULL"), nullable=True
    )
    # "chat" for a normal message, "progress" for a status/progress update —
    # lets clients render agent progress updates distinctly from chat.
    agent_message_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Relationships
    channel = relationship("Channel", back_populates="messages")
    user = relationship("User", back_populates="messages")
    agent_integration = relationship("AgentIntegration")
    parent = relationship("Message", remote_side="Message.id", backref="replies")
    attachments = relationship("Attachment", back_populates="message", lazy="selectin")
    reactions = relationship("MessageReaction", back_populates="message", lazy="selectin", cascade="all, delete-orphan")
    
    @property
    def is_edited(self) -> bool:
        return self.edited_at is not None
    
    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
    
    @property
    def is_external(self) -> bool:
        """Check if this message is from an external source (Slack/Discord/Agent)."""
        return self.external_source is not None

    @property
    def is_agent(self) -> bool:
        return self.external_source == ExternalSource.AGENT.value
    
    @property
    def content(self) -> str:
        """Alias for body field for compatibility."""
        return self.body
    
    @property
    def external_platform_name(self) -> str | None:
        """Get human-readable platform name."""
        if self.external_source == "slack":
            return "Slack"
        elif self.external_source == "discord":
            return "Discord"
        elif self.external_source == "agent":
            return self.external_author_name or "AI Agent"
        return None
    
    def soft_delete(self) -> None:
        """Soft delete the message."""
        from datetime import timezone
        self.deleted_at = datetime.now(timezone.utc)
    
    def __repr__(self) -> str:
        return f"<Message {self.id} by user {self.user_id}>"
