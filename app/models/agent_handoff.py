"""
AgentHandoff model: a gated request from one agent (or a human) to another
agent, so agents can coordinate on work — e.g. a Claude Code instance
working on the backend asking a Codex instance for frontend guidance, or
two Claude Code instances splitting frontend/backend on the same feature.

Every hand-off plays out as a normal message thread in a Forge channel
(anchored to root_message_id) so humans can watch, and it stays gated by
human approval before the target agent ever sees the prompt — nothing
consumes the target agent's tokens until a person clicks Approve.

Guardrails against runaway agent-to-agent loops:
- max_turns caps how many request/response round trips a single handoff
  chain can go through before it forces a fresh human approval.
- Every follow-up in a chain (parent_handoff_id) still requires its own
  approval; auto-chaining without a human is not supported by this model.
"""

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class HandoffStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"
    APPROVED = "approved"      # approved, waiting for target agent to pick it up
    DELIVERED = "delivered"    # target agent has fetched/received it
    RESPONDED = "responded"    # target agent posted a response
    EXPIRED = "expired"        # sat unapproved past the expiry window


class AgentHandoff(Base, TimestampMixin):
    """A single gated request from one agent (or human) to another agent."""

    __tablename__ = "agent_handoffs"

    id: Mapped[int] = mapped_column(primary_key=True)

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Requester: either an agent or a human (exactly one is set)
    from_agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_integrations.id", ondelete="SET NULL"), nullable=True
    )
    from_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Target: always an agent
    to_agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_integrations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=HandoffStatus.PENDING_APPROVAL.value, nullable=False, index=True)

    # The message this handoff request was announced as (visible in-channel,
    # with Approve/Reject affordances). The eventual response posts as a
    # thread reply to this message, so the whole exchange reads as one thread.
    root_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    response_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )

    approved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Threading: links a follow-up hand-off back to the one that spawned it,
    # so a whole back-and-forth is one countable chain for max_turns.
    parent_handoff_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_handoffs.id", ondelete="SET NULL"), nullable=True
    )
    turn_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_turns: Mapped[int] = mapped_column(Integer, default=4, nullable=False)

    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace = relationship("Workspace")
    channel = relationship("Channel")
    from_agent = relationship("AgentIntegration", foreign_keys=[from_agent_id])
    to_agent = relationship("AgentIntegration", foreign_keys=[to_agent_id])
    from_user = relationship("User", foreign_keys=[from_user_id])
    approved_by = relationship("User", foreign_keys=[approved_by_user_id])
    root_message = relationship("Message", foreign_keys=[root_message_id])
    response_message = relationship("Message", foreign_keys=[response_message_id])
    parent_handoff = relationship("AgentHandoff", remote_side="AgentHandoff.id")

    @property
    def is_chain_exhausted(self) -> bool:
        return self.turn_number >= self.max_turns

    def approve(self, user_id: int) -> None:
        self.status = HandoffStatus.APPROVED.value
        self.approved_by_user_id = user_id
        self.approved_at = datetime.now(timezone.utc)

    def reject(self, user_id: int, reason: str | None = None) -> None:
        self.status = HandoffStatus.REJECTED.value
        self.approved_by_user_id = user_id
        self.approved_at = datetime.now(timezone.utc)
        self.rejected_reason = reason

    def mark_delivered(self) -> None:
        self.status = HandoffStatus.DELIVERED.value
        self.delivered_at = datetime.now(timezone.utc)

    def mark_responded(self, response_message_id: int) -> None:
        self.status = HandoffStatus.RESPONDED.value
        self.response_message_id = response_message_id
        self.responded_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return f"<AgentHandoff {self.id} to_agent={self.to_agent_id} status={self.status}>"
