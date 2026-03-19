"""
User model for authentication and identity.
"""

import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin
from app.settings import settings


class AuthProvider(str, Enum):
    LOCAL = "local"
    GOOGLE = "google"
    BUILDLY = "buildly"


class UserStatus(str, Enum):
    ACTIVE = "active"
    AWAY = "away"
    DND = "dnd"  # Do not disturb
    OFFLINE = "offline"


class User(Base, TimestampMixin):
    """User account model."""
    
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # Profile fields
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Job title
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(50), nullable=True, default="UTC")
    status: Mapped[UserStatus] = mapped_column(String(20), default=UserStatus.ACTIVE, nullable=False)
    status_message: Mapped[str | None] = mapped_column(String(100), nullable=True)
    
    # Local auth
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # OAuth
    auth_provider: Mapped[AuthProvider] = mapped_column(
        String(20), 
        default=AuthProvider.LOCAL,
        nullable=False,
    )
    provider_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)  # OAuth subject ID
    
    # Buildly Labs SSO - for cross-app identity sync
    labs_user_id: Mapped[int | None] = mapped_column(nullable=True)  # Labs numeric user ID
    labs_org_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)  # Labs organization UUID
    labs_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # OAuth access token for Labs API
    labs_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # OAuth refresh token
    labs_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Buildly CollabHub - community profile sync (shares Labs identity)
    collabhub_user_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)  # CollabHub user UUID
    collabhub_org_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)  # CollabHub organization UUID
    collabhub_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # Last sync
    
    # Social profiles - shared across Labs/CollabHub ecosystem
    github_url: Mapped[str | None] = mapped_column(String(255), nullable=True)  # GitHub profile URL
    linkedin_url: Mapped[str | None] = mapped_column(String(255), nullable=True)  # LinkedIn profile URL
    twitter_url: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Twitter/X profile URL
    website_url: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Personal website URL
    
    # Public stats (synced from CollabHub)
    community_reputation: Mapped[int | None] = mapped_column(nullable=True, default=0)  # Community reputation score
    projects_count: Mapped[int | None] = mapped_column(nullable=True, default=0)  # Number of projects
    contributions_count: Mapped[int | None] = mapped_column(nullable=True, default=0)  # Number of contributions
    
    # Team memberships (synced from CollabHub)
    collabhub_roles: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"community": "member", "dev_team": true, "customer": true}
    
    # Google Workspace integration - for calendar-based status
    google_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Google subject ID
    google_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # OAuth access token
    google_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # OAuth refresh token
    google_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    google_calendar_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # Calendar-derived status
    google_calendar_message: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Calendar status message
    google_calendar_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # Last sync
    
    # Avatar
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # Google URLs can be very long
    
    # Session management
    session_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Status
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(default=False, nullable=False)  # Platform-wide admin
    
    # Account approval (for admin-controlled onboarding)
    is_approved: Mapped[bool] = mapped_column(default=True, nullable=False)  # Whether account is approved
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    can_create_workspaces: Mapped[bool] = mapped_column(default=True, nullable=False)  # Permission to create workspaces
    
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    memberships = relationship("Membership", back_populates="user", lazy="selectin")
    messages = relationship("Message", back_populates="user", lazy="noload")
    push_subscriptions = relationship("PushSubscription", back_populates="user", lazy="noload")
    notes = relationship("Note", back_populates="owner", foreign_keys="Note.owner_id", lazy="noload")
    external_integrations = relationship("ExternalIntegration", back_populates="user", lazy="noload")
    notification_logs = relationship("NotificationLog", back_populates="user", lazy="noload")
    sessions = relationship("UserSession", back_populates="user", lazy="noload", cascade="all, delete-orphan")
    attachments = relationship("Attachment", back_populates="user", lazy="noload")
    approved_by = relationship("User", remote_side="User.id", foreign_keys=[approved_by_id], lazy="joined")
    
    def generate_session_token(self) -> str:
        """Generate a new session token and set expiry."""
        self.session_token = secrets.token_hex(32)
        self.session_expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.session_expire_hours)
        return self.session_token
    
    def clear_session(self) -> None:
        """Clear session token."""
        self.session_token = None
        self.session_expires_at = None
    
    def is_session_valid(self) -> bool:
        """Check if current session is valid."""
        if not self.session_token or not self.session_expires_at:
            return False
        return datetime.now(timezone.utc) < self.session_expires_at
    
    def update_last_seen(self) -> None:
        """Update last seen timestamp."""
        self.last_seen_at = datetime.now(timezone.utc)
    
    @property
    def has_google_linked(self) -> bool:
        """Check if user has Google account linked."""
        return bool(self.google_refresh_token)
    
    @property
    def google_token_expired(self) -> bool:
        """Check if Google access token has expired."""
        if not self.google_token_expires_at:
            return True
        return datetime.now(timezone.utc) >= self.google_token_expires_at
    
    def set_google_tokens(
        self, 
        access_token: str, 
        refresh_token: str | None, 
        expires_in: int,
        google_sub: str | None = None,
    ) -> None:
        """Store Google OAuth tokens."""
        self.google_access_token = access_token
        if refresh_token:  # Only update if provided (may not be in refresh response)
            self.google_refresh_token = refresh_token
        self.google_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        if google_sub:
            self.google_sub = google_sub
    
    def clear_google_tokens(self) -> None:
        """Remove Google account link."""
        self.google_sub = None
        self.google_access_token = None
        self.google_refresh_token = None
        self.google_token_expires_at = None
        self.google_calendar_status = None
        self.google_calendar_message = None
        self.google_calendar_synced_at = None
    
    def update_calendar_status(self, status: str, message: str | None) -> None:
        """Update calendar-derived status."""
        self.google_calendar_status = status
        self.google_calendar_message = message
        self.google_calendar_synced_at = datetime.now(timezone.utc)
    
    def get_effective_status(self) -> tuple[str, str | None]:
        """
        Get the user's effective status, considering calendar.
        
        Calendar status takes precedence for 'away' (vacation) and 'dnd' (meeting).
        User's manual status is used otherwise.
        
        Returns:
            Tuple of (status, status_message)
        """
        # If user manually set DND, respect it
        if self.status == UserStatus.DND:
            return self.status.value, self.status_message
        
        # If user is offline, respect it
        if self.status == UserStatus.OFFLINE:
            return self.status.value, self.status_message
        
        # Check calendar status (if linked and recently synced)
        if self.has_google_linked and self.google_calendar_synced_at:
            # Only use calendar status if synced within last 10 minutes
            sync_age = datetime.now(timezone.utc) - self.google_calendar_synced_at
            if sync_age.total_seconds() < 600:  # 10 minutes
                if self.google_calendar_status in ("away", "dnd"):
                    return self.google_calendar_status, self.google_calendar_message
        
        # Fall back to user's manual status
        return self.status.value, self.status_message
    
    @property
    def effective_status_value(self) -> str:
        """Get effective status value for template display."""
        status, _ = self.get_effective_status()
        return status
    
    @property
    def effective_status_message(self) -> str | None:
        """Get effective status message for template display."""
        _, message = self.get_effective_status()
        return message
    
    @property
    def effective_status_emoji(self) -> str:
        """Get emoji for effective status."""
        status = self.effective_status_value
        return {
            "active": "🟢",
            "away": "🟡",
            "dnd": "🔴",
            "offline": "⚫",
        }.get(status, "⚫")
    
    @property
    def effective_status_css_class(self) -> str:
        """Get CSS class for effective status badge."""
        status = self.effective_status_value
        return {
            "active": "bg-green-500/20 text-green-400 border border-green-500/30",
            "away": "bg-yellow-500/20 text-yellow-400 border border-yellow-500/30",
            "dnd": "bg-red-500/20 text-red-400 border border-red-500/30",
            "offline": "bg-gray-500/20 text-gray-400 border border-gray-500/30",
        }.get(status, "bg-gray-500/20 text-gray-400 border border-gray-500/30")
    
    @property
    def is_in_meeting_from_calendar(self) -> bool:
        """Check if user is in a meeting according to calendar."""
        return (
            self.has_google_linked 
            and self.google_calendar_status == "dnd" 
            and self.google_calendar_message == "In a meeting"
        )
    
    @property 
    def is_on_vacation_from_calendar(self) -> bool:
        """Check if user is on vacation according to calendar."""
        return (
            self.has_google_linked
            and self.google_calendar_status == "away"
            and self.google_calendar_message == "On vacation"
        )
    
    @property
    def has_collabhub_linked(self) -> bool:
        """Check if user has CollabHub account linked."""
        return bool(self.collabhub_user_uuid)
    
    @property
    def is_community_member(self) -> bool:
        """Check if user is a community member in CollabHub."""
        if not self.collabhub_roles:
            return False
        return self.collabhub_roles.get("community") is not None
    
    @property
    def is_dev_team_member(self) -> bool:
        """Check if user is on a dev team in CollabHub."""
        if not self.collabhub_roles:
            return False
        return self.collabhub_roles.get("dev_team", False)
    
    @property
    def is_customer(self) -> bool:
        """Check if user is a customer in CollabHub."""
        if not self.collabhub_roles:
            return False
        return self.collabhub_roles.get("customer", False)
    
    @property
    def social_profiles(self) -> dict[str, str | None]:
        """Get all social profile URLs."""
        return {
            "github": self.github_url,
            "linkedin": self.linkedin_url,
            "twitter": self.twitter_url,
            "website": self.website_url,
        }
    
    def update_from_collabhub(
        self,
        user_uuid: str | None = None,
        org_uuid: str | None = None,
        github_url: str | None = None,
        linkedin_url: str | None = None,
        twitter_url: str | None = None,
        website_url: str | None = None,
        reputation: int | None = None,
        projects: int | None = None,
        contributions: int | None = None,
        roles: dict | None = None,
    ) -> None:
        """Update user profile from CollabHub sync."""
        if user_uuid:
            self.collabhub_user_uuid = user_uuid
        if org_uuid:
            self.collabhub_org_uuid = org_uuid
        if github_url is not None:
            self.github_url = github_url
        if linkedin_url is not None:
            self.linkedin_url = linkedin_url
        if twitter_url is not None:
            self.twitter_url = twitter_url
        if website_url is not None:
            self.website_url = website_url
        if reputation is not None:
            self.community_reputation = reputation
        if projects is not None:
            self.projects_count = projects
        if contributions is not None:
            self.contributions_count = contributions
        if roles is not None:
            self.collabhub_roles = roles
        self.collabhub_synced_at = datetime.now(timezone.utc)
    
    def to_public_profile(self) -> dict:
        """Return public profile data for API responses."""
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "bio": self.bio,
            "title": self.title,
            "avatar_url": self.avatar_url,
            "status": self.effective_status_value,
            "status_message": self.effective_status_message,
            "social_profiles": self.social_profiles,
            "community_reputation": self.community_reputation,
            "projects_count": self.projects_count,
            "contributions_count": self.contributions_count,
            "is_community_member": self.is_community_member,
            "is_dev_team_member": self.is_dev_team_member,
            "is_customer": self.is_customer,
        }
    
    def __repr__(self) -> str:
        return f"<User {self.email}>"
