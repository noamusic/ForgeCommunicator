"""
Artifact model for decisions, features, issues, and tasks.
"""

from datetime import date
from enum import Enum

from sqlalchemy import ARRAY, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class ArtifactType(str, Enum):
    DECISION = "decision"
    FEATURE = "feature"
    ISSUE = "issue"
    TASK = "task"


class ArtifactStatus(str, Enum):
    # Common
    OPEN = "open"
    CLOSED = "closed"
    
    # Decision-specific
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    
    # Feature-specific
    IDEA = "idea"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    SHIPPED = "shipped"
    
    # Issue-specific
    NEW = "new"
    TRIAGED = "triaged"
    WONT_FIX = "wont_fix"
    
    # Task-specific
    TODO = "todo"
    DONE = "done"
    BLOCKED = "blocked"


class IssueSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TaskPriority(str, Enum):
    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Artifact(Base, TimestampMixin):
    """Artifact model for decisions, features, issues, and tasks."""
    
    __tablename__ = "artifacts"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id", ondelete="SET NULL"), nullable=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    
    # Type
    type: Mapped[ArtifactType] = mapped_column(String(20), nullable=False, index=True)
    
    # Content
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Status
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    
    # Tags
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String(50)), nullable=True)
    
    # Author
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # External references
    github_issue_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    buildly_item_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    
    # Source message (for artifacts created from messages)
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    
    # Issue-specific
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    
    # Task-specific
    assignee_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Set instead of assignee_user_id when this artifact is assigned to an AI
    # coding agent (Claude Code, Codex, etc.) rather than a human.
    assignee_agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_integrations.id", ondelete="SET NULL"), nullable=True
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    priority: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Relationships
    workspace = relationship("Workspace")
    channel = relationship("Channel", back_populates="artifacts")
    product = relationship("Product", back_populates="artifacts")
    author = relationship("User", foreign_keys=[created_by])
    assignee = relationship("User", foreign_keys=[assignee_user_id])
    assignee_agent = relationship("AgentIntegration", foreign_keys=[assignee_agent_id])
    source_message = relationship("Message")
    
    @classmethod
    def get_default_status(cls, artifact_type: ArtifactType) -> str:
        """Get default status for artifact type."""
        defaults = {
            ArtifactType.DECISION: ArtifactStatus.PROPOSED.value,
            ArtifactType.FEATURE: ArtifactStatus.IDEA.value,
            ArtifactType.ISSUE: ArtifactStatus.NEW.value,
            ArtifactType.TASK: ArtifactStatus.TODO.value,
        }
        return defaults.get(artifact_type, ArtifactStatus.OPEN.value)
    
    def __repr__(self) -> str:
        return f"<Artifact {self.type.value}: {self.title[:30]}>"
