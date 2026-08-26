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

from fastapi import APIRouter, Form, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps import CurrentUser, DBSession
from app.models.agent_handoff import AgentHandoff, HandoffStatus
from app.models.agent_integration import AgentIntegration
from app.models.artifact import Artifact, ArtifactStatus
from app.models.channel import Channel
from app.models.membership import ChannelMembership, Membership, MembershipRole
from app.models.message import ExternalSource, Message
from app.services.labs_sync import LabsSyncService
from app.settings import settings
from app.templates_config import templates

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
# HTML form endpoints — register/revoke coding agents from the "My Agents"
# workspace page (app/templates/ai/workspace_agents.html). Distinct from
# the JSON API above: these redirect back to that page and surface the
# freshly-generated token/secret exactly once via a query flag, since
# neither is ever stored anywhere the browser could re-fetch it.
# ---------------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/agents/new")
async def create_agent_integration_form(
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
    name: Annotated[str, Form()],
    provider: Annotated[str, Form()] = "custom",
    auth_mode: Annotated[str, Form()] = "token",  # "token" | "webhook" | "both"
):
    await _require_workspace_admin(workspace_id, user, db)

    agent = AgentIntegration.create(
        workspace_id=workspace_id,
        created_by=user.id,
        name=name,
        provider=provider,
        with_token=auth_mode in ("token", "both"),
        with_webhook_secret=auth_mode in ("webhook", "both"),
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    return RedirectResponse(
        url=f"/ai/workspace/{workspace_id}/agents?new_agent_id={agent.id}",
        status_code=303,
    )


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/reveal", response_class=HTMLResponse)
async def reveal_agent_credentials(
    request: Request,
    workspace_id: int,
    agent_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """One-time credential reveal, linked from the ?new_agent_id= redirect
    right after creation. Not otherwise reachable — token/secret aren't
    retrievable through any other route once this page is left."""
    await _require_workspace_admin(workspace_id, user, db)
    result = await db.execute(
        select(AgentIntegration).where(
            AgentIntegration.id == agent_id, AgentIntegration.workspace_id == workspace_id
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent integration not found")

    return templates.TemplateResponse(
        "ai/agent_credentials_reveal.html",
        {"request": request, "user": user, "workspace_id": workspace_id, "agent": agent},
    )


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/revoke-form")
async def revoke_agent_integration_form(workspace_id: int, agent_id: int, user: CurrentUser, db: DBSession):
    await _require_workspace_admin(workspace_id, user, db)
    result = await db.execute(
        select(AgentIntegration).where(
            AgentIntegration.id == agent_id, AgentIntegration.workspace_id == workspace_id
        )
    )
    agent = result.scalar_one_or_none()
    if agent:
        agent.revoke()
        await db.commit()
    return RedirectResponse(url=f"/ai/workspace/{workspace_id}/agents", status_code=303)


@router.post("/workspaces/{workspace_id}/handoffs/{handoff_id}/approve-form")
async def approve_handoff_form(workspace_id: int, handoff_id: int, user: CurrentUser, db: DBSession):
    result = await db.execute(
        select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a workspace member")

    result = await db.execute(
        select(AgentHandoff).where(AgentHandoff.id == handoff_id, AgentHandoff.workspace_id == workspace_id)
    )
    handoff = result.scalar_one_or_none()
    if handoff and handoff.status == HandoffStatus.PENDING_APPROVAL.value:
        handoff.approve(user.id)
        await db.commit()
        result = await db.execute(select(AgentIntegration).where(AgentIntegration.id == handoff.to_agent_id))
        to_agent = result.scalar_one_or_none()
        if to_agent:
            await _notify_agent_webhook(to_agent, "handoff.approved", {"handoff_id": handoff.id})

    return RedirectResponse(url=f"/ai/workspace/{workspace_id}/agents", status_code=303)


@router.post("/workspaces/{workspace_id}/handoffs/{handoff_id}/reject-form")
async def reject_handoff_form(
    workspace_id: int,
    handoff_id: int,
    user: CurrentUser,
    db: DBSession,
    reason: Annotated[str | None, Form()] = None,
):
    result = await db.execute(
        select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a workspace member")

    result = await db.execute(
        select(AgentHandoff).where(AgentHandoff.id == handoff_id, AgentHandoff.workspace_id == workspace_id)
    )
    handoff = result.scalar_one_or_none()
    if handoff and handoff.status == HandoffStatus.PENDING_APPROVAL.value:
        handoff.reject(user.id, reason=reason)
        await db.commit()

    return RedirectResponse(url=f"/ai/workspace/{workspace_id}/agents", status_code=303)


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


# ---------------------------------------------------------------------------
# Agent-to-agent hand-offs
#
# One agent (or a human) asks another agent for help — e.g. a Claude Code
# instance working the backend asking a Codex instance for frontend
# guidance, or two Claude Code instances splitting a feature. Every
# hand-off plays out as a normal channel thread so a human can watch it,
# and nothing reaches the target agent until a human approves it — no
# hand-off spends a token on the receiving agent without a person opting
# in first. A hard max_turns per chain stops an approved back-and-forth
# from looping indefinitely; once exhausted, a fresh hand-off (and fresh
# approval) is required to continue.
# ---------------------------------------------------------------------------


async def _notify_agent_webhook(agent: AgentIntegration, event: str, payload: dict) -> None:
    """Best-effort webhook push. Failure here just means the agent falls
    back to polling GET /agents/v1/inbox — it's never the only delivery path."""
    if not agent.webhook_url:
        return
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(agent.webhook_url, json={"event": event, **payload})
    except Exception:
        pass


class CreateHandoffRequest(BaseModel):
    to_agent_id: int
    channel_id: int
    prompt: str
    max_turns: int = 4
    parent_handoff_id: int | None = None


class HandoffResponse(BaseModel):
    id: int
    workspace_id: int
    channel_id: int
    from_agent_id: int | None
    from_user_id: int | None
    to_agent_id: int
    prompt: str
    status: str
    turn_number: int
    max_turns: int
    parent_handoff_id: int | None
    root_message_id: int | None
    response_message_id: int | None
    created_at: datetime


def _handoff_to_response(h: AgentHandoff) -> HandoffResponse:
    return HandoffResponse(
        id=h.id, workspace_id=h.workspace_id, channel_id=h.channel_id,
        from_agent_id=h.from_agent_id, from_user_id=h.from_user_id, to_agent_id=h.to_agent_id,
        prompt=h.prompt, status=h.status, turn_number=h.turn_number, max_turns=h.max_turns,
        parent_handoff_id=h.parent_handoff_id, root_message_id=h.root_message_id,
        response_message_id=h.response_message_id, created_at=h.created_at,
    )


async def _create_handoff_common(
    workspace_id: int,
    body: CreateHandoffRequest,
    db,
    from_agent: AgentIntegration | None,
    from_user_id: int | None,
) -> AgentHandoff:
    result = await db.execute(
        select(AgentIntegration).where(
            AgentIntegration.id == body.to_agent_id,
            AgentIntegration.workspace_id == workspace_id,
        )
    )
    to_agent = result.scalar_one_or_none()
    if not to_agent or not to_agent.is_valid:
        raise HTTPException(status_code=404, detail="Target agent not found in this workspace")

    result = await db.execute(
        select(Channel).where(Channel.id == body.channel_id, Channel.workspace_id == workspace_id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found in this workspace")

    turn_number = 1
    max_turns = body.max_turns
    if body.parent_handoff_id is not None:
        result = await db.execute(
            select(AgentHandoff).where(
                AgentHandoff.id == body.parent_handoff_id,
                AgentHandoff.workspace_id == workspace_id,
            )
        )
        parent = result.scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent hand-off not found")
        if parent.is_chain_exhausted:
            raise HTTPException(
                status_code=409,
                detail=f"This hand-off chain has reached its {parent.max_turns}-turn limit — "
                       f"a human needs to start a fresh hand-off to continue.",
            )
        turn_number = parent.turn_number + 1
        max_turns = parent.max_turns

    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    handoff = AgentHandoff(
        workspace_id=workspace_id,
        channel_id=body.channel_id,
        from_agent_id=from_agent.id if from_agent else None,
        from_user_id=from_user_id,
        to_agent_id=body.to_agent_id,
        prompt=prompt,
        status=HandoffStatus.PENDING_APPROVAL.value,
        turn_number=turn_number,
        max_turns=max_turns,
        parent_handoff_id=body.parent_handoff_id,
    )
    db.add(handoff)
    await db.flush()

    from_label = from_agent.name if from_agent else "A teammate"
    announce = Message(
        channel_id=body.channel_id,
        user_id=None,
        body=(
            f"🤝 **{from_label}** is requesting input from **{to_agent.name}** "
            f"(turn {turn_number}/{max_turns}):\n\n> {prompt}\n\n"
            f"_Awaiting approval — see this channel's Agent Requests panel._"
        ),
        external_source=ExternalSource.AGENT.value if from_agent else None,
        external_author_name=from_label if from_agent else None,
        agent_integration_id=from_agent.id if from_agent else None,
        agent_message_kind="handoff_request",
    )
    db.add(announce)
    await db.flush()
    handoff.root_message_id = announce.id

    await db.commit()
    await db.refresh(handoff)
    return handoff


@router.post(
    "/agents/v1/handoffs",
    response_model=HandoffResponse,
    status_code=201,
)
async def agent_create_handoff(
    body: CreateHandoffRequest,
    request: Request,
    db: DBSession,
    authorization: Annotated[str | None, Header()] = None,
    x_forge_agent_id: Annotated[str | None, Header(alias="X-Forge-Agent-Id")] = None,
    x_forge_agent_timestamp: Annotated[str | None, Header(alias="X-Forge-Agent-Timestamp")] = None,
    x_forge_agent_signature: Annotated[str | None, Header(alias="X-Forge-Agent-Signature")] = None,
):
    """An agent requests input/work from another agent. Requires human
    approval before the target agent ever sees the prompt."""
    agent = await get_agent(
        request, db, authorization, x_forge_agent_id, x_forge_agent_timestamp, x_forge_agent_signature
    )
    await _channel_in_scope(agent, body.channel_id, db)
    handoff = await _create_handoff_common(agent.workspace_id, body, db, from_agent=agent, from_user_id=None)
    return _handoff_to_response(handoff)


@router.post(
    "/workspaces/{workspace_id}/handoffs",
    response_model=HandoffResponse,
    status_code=201,
)
async def human_create_handoff(
    workspace_id: int,
    body: CreateHandoffRequest,
    user: CurrentUser,
    db: DBSession,
):
    """A human directly asks an agent for help (same approval-gated flow,
    but self-approved since the human is already the one asking)."""
    result = await db.execute(
        select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a workspace member")

    handoff = await _create_handoff_common(workspace_id, body, db, from_agent=None, from_user_id=user.id)
    handoff.approve(user.id)
    await db.commit()
    await db.refresh(handoff)

    result = await db.execute(select(AgentIntegration).where(AgentIntegration.id == handoff.to_agent_id))
    to_agent = result.scalar_one_or_none()
    if to_agent:
        await _notify_agent_webhook(to_agent, "handoff.approved", {"handoff_id": handoff.id})

    return _handoff_to_response(handoff)


@router.get(
    "/workspaces/{workspace_id}/handoffs",
    response_model=list[HandoffResponse],
)
async def list_handoffs(
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
    status_filter: str | None = Query(None, alias="status"),
):
    """List hand-offs in a workspace — powers the Agent Requests panel
    where humans approve/reject pending ones."""
    result = await db.execute(
        select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a workspace member")

    filters = [AgentHandoff.workspace_id == workspace_id]
    if status_filter:
        filters.append(AgentHandoff.status == status_filter)
    result = await db.execute(
        select(AgentHandoff).where(*filters).order_by(AgentHandoff.created_at.desc()).limit(200)
    )
    return [_handoff_to_response(h) for h in result.scalars().all()]


class HandoffDecisionRequest(BaseModel):
    reason: str | None = None


@router.post(
    "/workspaces/{workspace_id}/handoffs/{handoff_id}/approve",
    response_model=HandoffResponse,
)
async def approve_handoff(
    workspace_id: int,
    handoff_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Human approves a pending hand-off — this is the gate that lets the
    target agent's tokens actually get spent."""
    result = await db.execute(
        select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a workspace member")

    result = await db.execute(
        select(AgentHandoff).where(AgentHandoff.id == handoff_id, AgentHandoff.workspace_id == workspace_id)
    )
    handoff = result.scalar_one_or_none()
    if not handoff:
        raise HTTPException(status_code=404, detail="Hand-off not found")
    if handoff.status != HandoffStatus.PENDING_APPROVAL.value:
        raise HTTPException(status_code=409, detail=f"Hand-off is already {handoff.status}")

    handoff.approve(user.id)
    await db.commit()
    await db.refresh(handoff)

    result = await db.execute(select(AgentIntegration).where(AgentIntegration.id == handoff.to_agent_id))
    to_agent = result.scalar_one_or_none()
    if to_agent:
        await _notify_agent_webhook(to_agent, "handoff.approved", {"handoff_id": handoff.id})

    return _handoff_to_response(handoff)


@router.post(
    "/workspaces/{workspace_id}/handoffs/{handoff_id}/reject",
    response_model=HandoffResponse,
)
async def reject_handoff(
    workspace_id: int,
    handoff_id: int,
    body: HandoffDecisionRequest,
    user: CurrentUser,
    db: DBSession,
):
    """Human rejects a pending hand-off — the target agent never sees it."""
    result = await db.execute(
        select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a workspace member")

    result = await db.execute(
        select(AgentHandoff).where(AgentHandoff.id == handoff_id, AgentHandoff.workspace_id == workspace_id)
    )
    handoff = result.scalar_one_or_none()
    if not handoff:
        raise HTTPException(status_code=404, detail="Hand-off not found")
    if handoff.status != HandoffStatus.PENDING_APPROVAL.value:
        raise HTTPException(status_code=409, detail=f"Hand-off is already {handoff.status}")

    handoff.reject(user.id, reason=body.reason)
    await db.commit()
    await db.refresh(handoff)
    return _handoff_to_response(handoff)


@router.get(
    "/agents/v1/inbox",
    response_model=list[HandoffResponse],
)
async def agent_inbox(
    request: Request,
    db: DBSession,
    authorization: Annotated[str | None, Header()] = None,
    x_forge_agent_id: Annotated[str | None, Header(alias="X-Forge-Agent-Id")] = None,
    x_forge_agent_timestamp: Annotated[str | None, Header(alias="X-Forge-Agent-Timestamp")] = None,
    x_forge_agent_signature: Annotated[str | None, Header(alias="X-Forge-Agent-Signature")] = None,
):
    """Poll for approved hand-offs addressed to this agent that haven't
    been picked up yet. Agents that didn't register a webhook use this."""
    agent = await get_agent(
        request, db, authorization, x_forge_agent_id, x_forge_agent_timestamp, x_forge_agent_signature
    )
    result = await db.execute(
        select(AgentHandoff)
        .where(AgentHandoff.to_agent_id == agent.id, AgentHandoff.status == HandoffStatus.APPROVED.value)
        .order_by(AgentHandoff.created_at.asc())
    )
    handoffs = result.scalars().all()
    for h in handoffs:
        h.mark_delivered()
    if handoffs:
        await db.commit()
    return [_handoff_to_response(h) for h in handoffs]


class HandoffRespondRequest(BaseModel):
    response: str


@router.post(
    "/agents/v1/handoffs/{handoff_id}/respond",
    response_model=HandoffResponse,
)
async def agent_respond_handoff(
    handoff_id: int,
    body: HandoffRespondRequest,
    request: Request,
    db: DBSession,
    authorization: Annotated[str | None, Header()] = None,
    x_forge_agent_id: Annotated[str | None, Header(alias="X-Forge-Agent-Id")] = None,
    x_forge_agent_timestamp: Annotated[str | None, Header(alias="X-Forge-Agent-Timestamp")] = None,
    x_forge_agent_signature: Annotated[str | None, Header(alias="X-Forge-Agent-Signature")] = None,
):
    """Target agent posts its answer, closing this hand-off. The response
    is posted as a reply in the same thread as the original request so
    the whole exchange reads naturally in the channel."""
    agent = await get_agent(
        request, db, authorization, x_forge_agent_id, x_forge_agent_timestamp, x_forge_agent_signature
    )

    result = await db.execute(
        select(AgentHandoff).where(AgentHandoff.id == handoff_id, AgentHandoff.to_agent_id == agent.id)
    )
    handoff = result.scalar_one_or_none()
    if not handoff:
        raise HTTPException(status_code=404, detail="Hand-off not found or not addressed to this agent")
    if handoff.status not in (HandoffStatus.APPROVED.value, HandoffStatus.DELIVERED.value):
        raise HTTPException(status_code=409, detail=f"Hand-off is not awaiting a response (status={handoff.status})")

    text = body.response.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Response is required")

    reply = Message(
        channel_id=handoff.channel_id,
        user_id=None,
        body=text,
        parent_id=handoff.root_message_id,
        external_source=ExternalSource.AGENT.value,
        external_author_name=agent.name,
        agent_integration_id=agent.id,
        agent_message_kind="handoff_response",
    )
    db.add(reply)

    if handoff.root_message_id:
        result = await db.execute(select(Message).where(Message.id == handoff.root_message_id))
        root = result.scalar_one_or_none()
        if root:
            root.thread_reply_count = (root.thread_reply_count or 0) + 1

    await db.flush()
    handoff.mark_responded(reply.id)
    await db.commit()
    await db.refresh(handoff)

    return _handoff_to_response(handoff)
