"""
Membership model for workspace and channel memberships.
"""

from enum import Enum

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class MembershipRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    GUEST = "guest"


class Membership(Base, TimestampMixin):
    """Workspace membership model."""
    
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_membership_workspace_user"),
    )
    
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(String(20), default=MembershipRole.MEMBER, nullable=False)
    
    # Notification preferences
    notifications_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    notify_all_messages: Mapped[bool] = mapped_column(default=False, nullable=False)
    
    # Relationships
    workspace = relationship("Workspace", back_populates="memberships")
    user = relationship("User", back_populates="memberships")
    
    def __repr__(self) -> str:
        return f"<Membership user={self.user_id} workspace={self.workspace_id} role={self.role}>"


class ChannelMembership(Base, TimestampMixin):
    """Channel membership model (for private channels)."""
    
    __tablename__ = "channel_memberships"
    __table_args__ = (
        UniqueConstraint("channel_id", "user_id", name="uq_channel_membership_channel_user"),
    )
    
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Last read tracking
    last_read_message_id: Mapped[int | None] = mapped_column(nullable=True)
    
    # Relationships
    channel = relationship("Channel", back_populates="memberships")
    user = relationship("User")
    
    def __repr__(self) -> str:
        return f"<ChannelMembership user={self.user_id} channel={self.channel_id}>"


class ThreadReadState(Base, TimestampMixin):
    """Track when users have read thread replies."""
    
    __tablename__ = "thread_read_states"
    __table_args__ = (
        UniqueConstraint("user_id", "parent_message_id", name="uq_thread_read_user_message"),
    )
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    parent_message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    last_read_reply_id: Mapped[int | None] = mapped_column(nullable=True)  # Last reply the user has seen
    
    def __repr__(self) -> str:
        return f"<ThreadReadState user={self.user_id} parent={self.parent_message_id}>"
