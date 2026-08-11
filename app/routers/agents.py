"""
Third-party AI coding agent integration (Claude Code, Codex, custom bots).

Two surfaces:
1. Admin endpoints (session-auth, under /workspaces/{id}/agents) to create,
   list, and revoke AgentIntegration records from Forge itself.
2. Agent-facing endpoints (under /agents/v1) authenticated as the agent
   itself — not any human user — via either a bearer token or an
   HMAC-signed request. These let the agent post chat messages, post
   progress updates, list/claim assigned artifacts, and update artifact
   status (optionally syncing back to Buildly Labs).

Agent messages are stored as ordinary Message rows with
external_source="agent" and user_id=None, the same representation used
for bridged Slack/Discord messages, so they show up in conversation
lists, unread counts, and both the web and native UIs without any
special-casing beyond a distinct render style.
"""

import time
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps import CurrentUser, DBSession
from app.models.agent_integration import AgentIntegration
from app.models.artifact import Artifact, ArtifactStatus
from app.models.channel import Channel
from app.models.membership import ChannelMembership, Membership
from app.models.message import ExternalSource, Message
from app.services.labs_sync import LabsSyncService
from app.settings import settings

router = APIRouter(tags=["agents"])


# ---------------------------------------------------------------------------
# Admin endpoints — manage agent integrations from Forge (session auth)
# ---------------------------------------------------------------------------


class CreateAgentRequest(BaseModel):
    name: str
    provider: str = "custom"  # "claude_code" | "codex" | "custom"
    avatar_emoji: str | None = None
    allowed_channel_ids: list[int] | None = None
    with_token: bool = True
    with_webhook_secret: bool = False
    buildly_product_uuid: str | None = None


class AgentIntegrationResponse(BaseModel):
    id: int
    workspace_id: int
    name: str
    provider: str
    avatar_emoji: str | None
    is_active: bool
    allowed_channel_ids: list[int] | None
    last_used_at: datetime | None
    created_at: datetime
    buildly_product_uuid: str | None
    # Only populated once, at creation time — never returned again.
    token: str | None = None
    webhook_secret: str | None = None


async def _require_workspace_admin(workspace_id: int, user, db) -> None:
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=403, detail="Not a workspace member")
    if membership.role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Workspace admin required")


@router.post(
    "/workspaces/{workspace_id}/agents",
    response_model=AgentIntegrationResponse,
    status_code=201,
)
async def create_agent_integration(
    workspace_id: int,
    body: CreateAgentRequest,
    user: CurrentUser,
    db: DBSession,
):
    """Register a new AI agent integration for this workspace."""
    await _require_workspace_admin(workspace_id, user, db)

    agent = AgentIntegration.create(
        workspace_id=workspace_id,
        created_by=user.id,
        name=body.name,
        provider=body.provider,
        with_token=body.with_token,
        with_webhook_secret=body.with_webhook_secret,
        allowed_channel_ids=body.allowed_channel_ids,
    )
    agent.avatar_emoji = body.avatar_emoji
    agent.buildly_product_uuid = body.buildly_product_uuid
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    return AgentIntegrationResponse(
        id=agent.id, workspace_id=agent.workspace_id, name=agent.name,
        provider=agent.provider, avatar_emoji=agent.avatar_emoji,
        is_active=agent.is_active, allowed_channel_ids=agent.allowed_channel_ids,
        last_used_at=agent.last_used_at, created_at=agent.created_at,
        buildly_product_uuid=agent.buildly_product_uuid,
        token=agent.token, webhook_secret=agent.webhook_secret,
    )


@router.get(
    "/workspaces/{workspace_id}/agents",
    response_model=list[AgentIntegrationResponse],
)
async def list_agent_integrations(workspace_id: int, user: CurrentUser, db: DBSession):
    await _require_workspace_admin(workspace_id, user, db)
    result = await db.execute(
        select(AgentIntegration).where(AgentIntegration.workspace_id == workspace_id)
        .order_by(AgentIntegration.created_at.desc())
    )
    agents = result.scalars().all()
    return [
        AgentIntegrationResponse(
            id=a.id, workspace_id=a.workspace_id, name=a.name, provider=a.provider,
            avatar_emoji=a.avatar_emoji, is_active=a.is_active,
            allowed_channel_ids=a.allowed_channel_ids, last_used_at=a.last_used_at,
            created_at=a.created_at, buildly_product_uuid=a.buildly_product_uuid,
            token=None, webhook_secret=None,  # never re-exposed after creation
        )
        for a in agents
    ]


@router.delete("/workspaces/{workspace_id}/agents/{agent_id}", status_code=204)
async def revoke_agent_integration(workspace_id: int, agent_id: int, user: CurrentUser, db: DBSession):
    await _require_workspace_admin(workspace_id, user, db)
    result = await db.execute(
        select(AgentIntegration).where(
            AgentIntegration.id == agent_id,
            AgentIntegration.workspace_id == workspace_id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent integration not found")
    agent.revoke()
    await db.commit()


# ---------------------------------------------------------------------------
# Agent-facing endpoints — authenticate as the agent itself
# ---------------------------------------------------------------------------


async def get_agent(
    request: Request,
    db,
    authorization: str | None,
    x_forge_agent_id: str | None,
    x_forge_agent_timestamp: str | None,
    x_forge_agent_signature: str | None,
) -> AgentIntegration:
    """Resolve the calling AgentIntegration via bearer token or HMAC signature.

    Bearer: `Authorization: Bearer agt_...`
    HMAC:   `X-Forge-Agent-Id`, `X-Forge-Agent-Timestamp`, `X-Forge-Agent-Signature`
            over the raw request body, using the integration's webhook_secret.
    """
    agent: AgentIntegration | None = None

    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        result = await db.execute(select(AgentIntegration).where(AgentIntegration.token == token))
        agent = result.scalar_one_or_none()
    elif x_forge_agent_id and x_forge_agent_signature and x_forge_agent_timestamp:
        result = await db.execute(
            select(AgentIntegration).where(AgentIntegration.id == int(x_forge_agent_id))
        )
        candidate = result.scalar_one_or_none()
        if candidate:
            # Reject stale signatures (> 5 minutes) to limit replay window.
            try:
                ts = int(x_forge_agent_timestamp)
            except ValueError:
                ts = 0
            if abs(time.time() - ts) > 300:
                raise HTTPException(status_code=401, detail="Signature timestamp expired")

            body = await request.body()
            if candidate.verify_signature(body, x_forge_agent_timestamp, x_forge_agent_signature):
                agent = candidate

    if not agent or not agent.is_valid:
        raise HTTPException(status_code=401, detail="Invalid or revoked agent credentials")

    agent.touch()
    await db.commit()
    return agent


async def _channel_in_scope(agent: AgentIntegration, channel_id: int, db) -> Channel:
    result = await db.execute(
        select(Channel).where(Channel.id == channel_id, Channel.workspace_id == agent.workspace_id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found in this workspace")
    if not agent.can_access_channel(channel_id):
        raise HTTPException(status_code=403, detail="Agent is not scoped to this channel")
    return channel


class AgentMessageRequest(BaseModel):
    body: str
    parent_id: int | None = None


class AgentProgressRequest(BaseModel):
    body: str
    artifact_id: int | None = None  # optional: ties this update to an issue/feature


class AgentMessageResponse(BaseModel):
    id: int
    channel_id: int
    body: str
    created_at: datetime
    agent_name: str
    kind: str


@router.post(
    "/agents/v1/channels/{channel_id}/messages",
    response_model=AgentMessageResponse,
    status_code=201,
)
async def agent_post_message(
    channel_id: int,
    body: AgentMessageRequest,
    request: Request,
    db: DBSession,
    authorization: Annotated[str | None, Header()] = None,
    x_forge_agent_id: Annotated[str | None, Header(alias="X-Forge-Agent-Id")] = None,
    x_forge_agent_timestamp: Annotated[str | None, Header(alias="X-Forge-Agent-Timestamp")] = None,
    x_forge_agent_signature: Annotated[str | None, Header(alias="X-Forge-Agent-Signature")] = None,
):
    """Post a chat message into a channel as this agent."""
    agent = await get_agent(
        request, db, authorization, x_forge_agent_id, x_forge_agent_timestamp, x_forge_agent_signature
    )
    await _channel_in_scope(agent, channel_id, db)

    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message body is required")

    msg = Message(
        channel_id=channel_id,
        user_id=None,
        body=text,
        parent_id=body.parent_id,
        external_source=ExternalSource.AGENT.value,
        external_author_name=agent.name,
        agent_integration_id=agent.id,
        agent_message_kind="chat",
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    return AgentMessageResponse(
        id=msg.id, channel_id=msg.channel_id, body=msg.body,
        created_at=msg.created_at, agent_name=agent.name, kind="chat",
    )


@router.post(
    "/agents/v1/channels/{channel_id}/progress",
    response_model=AgentMessageResponse,
    status_code=201,
)
async def agent_post_progress(
    channel_id: int,
    body: AgentProgressRequest,
    request: Request,
    db: DBSession,
    authorization: Annotated[str | None, Header()] = None,
    x_forge_agent_id: Annotated[str | None, Header(alias="X-Forge-Agent-Id")] = None,
    x_forge_agent_timestamp: Annotated[str | None, Header(alias="X-Forge-Agent-Timestamp")] = None,
    x_forge_agent_signature: Annotated[str | None, Header(alias="X-Forge-Agent-Signature")] = None,
):
    """Post a progress/status update, optionally tied to an artifact (issue/feature/task).

    Rendered distinctly from chat messages ("agent_message_kind=progress") so
    clients can show a compact status line instead of a full chat bubble.
    """
    agent = await get_agent(
        request, db, authorization, x_forge_agent_id, x_forge_agent_timestamp, x_forge_agent_signature
    )
    await _channel_in_scope(agent, channel_id, db)

    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Progress body is required")

    if body.artifact_id is not None:
        result = await db.execute(
            select(Artifact).where(Artifact.id == body.artifact_id, Artifact.workspace_id == agent.workspace_id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Artifact not found in this workspace")

    msg = Message(
        channel_id=channel_id,
        user_id=None,
        body=text,
        external_source=ExternalSource.AGENT.value,
        external_author_name=agent.name,
        agent_integration_id=agent.id,
        agent_message_kind="progress",
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    return AgentMessageResponse(
        id=msg.id, channel_id=msg.channel_id, body=msg.body,
        created_at=msg.created_at, agent_name=agent.name, kind="progress",
    )


class AgentArtifactResponse(BaseModel):
    id: int
    workspace_id: int
    channel_id: int | None
    type: str
    title: str
    body: str | None
    status: str
    priority: str | None
    buildly_item_uuid: str | None


@router.get(
    "/agents/v1/artifacts",
    response_model=list[AgentArtifactResponse],
)
async def agent_list_assigned_artifacts(
    request: Request,
    db: DBSession,
    authorization: Annotated[str | None, Header()] = None,
    x_forge_agent_id: Annotated[str | None, Header(alias="X-Forge-Agent-Id")] = None,
    x_forge_agent_timestamp: Annotated[str | None, Header(alias="X-Forge-Agent-Timestamp")] = None,
    x_forge_agent_signature: Annotated[str | None, Header(alias="X-Forge-Agent-Signature")] = None,
    status_filter: str | None = Query(None, alias="status"),
):
    """List artifacts (issues/features/tasks) assigned to this agent."""
    agent = await get_agent(
        request, db, authorization, x_forge_agent_id, x_forge_agent_timestamp, x_forge_agent_signature
    )

    filters = [Artifact.workspace_id == agent.workspace_id, Artifact.assignee_agent_id == agent.id]
    if status_filter:
        filters.append(Artifact.status == status_filter)

    result = await db.execute(select(Artifact).where(*filters).order_by(Artifact.created_at.desc()))
    artifacts = result.scalars().all()
    return [
        AgentArtifactResponse(
            id=a.id, workspace_id=a.workspace_id, channel_id=a.channel_id, type=a.type,
            title=a.title, body=a.body, status=a.status, priority=a.priority,
            buildly_item_uuid=a.buildly_item_uuid,
        )
        for a in artifacts
    ]


class AgentArtifactUpdateRequest(BaseModel):
    status: str
    sync_to_labs: bool = True


@router.patch(
    "/agents/v1/artifacts/{artifact_id}",
    response_model=AgentArtifactResponse,
)
async def agent_update_artifact_status(
    artifact_id: int,
    body: AgentArtifactUpdateRequest,
    request: Request,
    db: DBSession,
    authorization: Annotated[str | None, Header()] = None,
    x_forge_agent_id: Annotated[str | None, Header(alias="X-Forge-Agent-Id")] = None,
    x_forge_agent_timestamp: Annotated[str | None, Header(alias="X-Forge-Agent-Timestamp")] = None,
    x_forge_agent_signature: Annotated[str | None, Header(alias="X-Forge-Agent-Signature")] = None,
):
    """Update the status of an artifact assigned to this agent, optionally
    pushing the change back to Buildly Labs via the existing sync service."""
    agent = await get_agent(
        request, db, authorization, x_forge_agent_id, x_forge_agent_timestamp, x_forge_agent_signature
    )

    result = await db.execute(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.workspace_id == agent.workspace_id,
            Artifact.assignee_agent_id == agent.id,
        )
    )
    artifact = result.scalar_one_or_none()
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found or not assigned to this agent")

    artifact.status = body.status
    await db.commit()
    await db.refresh(artifact)

    if body.sync_to_labs and artifact.buildly_item_uuid and settings.labs_api_key:
        try:
            labs = LabsSyncService(api_key=settings.labs_api_key)
            await labs.update_backlog_item(artifact.buildly_item_uuid, status=body.status)
        except Exception:
            # Labs push is best-effort — the local status change already committed.
            pass

    return AgentArtifactResponse(
        id=artifact.id, workspace_id=artifact.workspace_id, channel_id=artifact.channel_id,
        type=artifact.type, title=artifact.title, body=artifact.body, status=artifact.status,
        priority=artifact.priority, buildly_item_uuid=artifact.buildly_item_uuid,
    )
