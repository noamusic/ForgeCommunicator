"""
AI Agent management router.

Provides endpoints for:
- Creating and managing AI agents (workspace and personal)
- AI conversations and chat
- Adding AI agents to channels
"""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.orm import selectinload

from app.deps import CurrentUser, DBSession
from app.models.ai_agent import (
    AIAgent,
    AIAgentScope,
    AIConversation,
    AIMessage,
    AIChannelMembership,
    AIProvider,
)
from app.models.membership import Membership, MembershipRole
from app.models.workspace import Workspace
from app.models.channel import Channel
from app.services.ai_service import AIAgentService
from app.services.ai_providers import get_available_models, DEFAULT_MODELS
from app.templates_config import templates


router = APIRouter(prefix="/ai", tags=["ai"])


# =============================================================================
# Pydantic Models
# =============================================================================

class CreateAgentRequest(BaseModel):
    """Request to create an AI agent."""
    name: str
    display_name: str
    provider: AIProvider
    api_key: str
    model: str | None = None
    description: str | None = None
    avatar_url: str | None = None
    system_prompt: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4096
    can_read_channels: bool = False
    can_read_artifacts: bool = False
    can_read_notes: bool = False


class SendMessageRequest(BaseModel):
    """Request to send a message to an AI agent."""
    message: str
    workspace_id: int | None = None
    channel_id: int | None = None


# =============================================================================
# Helper Functions
# =============================================================================

async def get_workspace_membership(
    workspace_id: int,
    user_id: int,
    db,
) -> tuple[Workspace, Membership]:
    """Helper to get workspace and verify membership."""
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")
    
    return workspace, membership


def check_agent_access(agent: AIAgent, user_id: int, membership: Membership | None = None) -> bool:
    """Check if user has access to an agent."""
    # User owns the agent
    if agent.owner_id == user_id:
        return True
    
    # Workspace agent and user is a member
    if agent.scope == AIAgentScope.WORKSPACE and membership:
        if membership.workspace_id == agent.workspace_id:
            return True
    
    return False


def check_agent_admin(agent: AIAgent, user_id: int, membership: Membership | None = None) -> bool:
    """Check if user can admin an agent (edit/delete)."""
    # User owns the agent
    if agent.owner_id == user_id:
        return True
    
    # Workspace agent and user is admin/owner
    if agent.scope == AIAgentScope.WORKSPACE and membership:
        if membership.workspace_id == agent.workspace_id:
            if membership.role in [MembershipRole.ADMIN, MembershipRole.OWNER]:
                return True
    
    return False


# =============================================================================
# Agent Management - Personal
# =============================================================================

@router.get("/agents", response_class=HTMLResponse)
async def list_my_agents(
    request: Request,
    user: CurrentUser,
    db: DBSession,
):
    """List user's personal AI agents."""
    service = AIAgentService(db)
    agents = await service.get_user_agents(user.id)
    
    return templates.TemplateResponse(
        "ai/agents_list.html",
        {
            "request": request,
            "user": user,
            "agents": agents,
            "providers": [p.value for p in AIProvider],
            "scope": "personal",
        },
    )


@router.get("/agents/new", response_class=HTMLResponse)
async def new_agent_form(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    workspace_id: int | None = None,
):
    """Show form to create a new AI agent."""
    workspace = None
    membership = None
    
    if workspace_id:
        workspace, membership = await get_workspace_membership(workspace_id, user.id, db)
        # Only admins/owners can create workspace agents
        if membership.role not in [MembershipRole.ADMIN, MembershipRole.OWNER]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    # Get available models per provider
    models_by_provider = {
        provider.value: get_available_models(provider)
        for provider in AIProvider
    }
    
    return templates.TemplateResponse(
        "ai/agent_form.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "membership": membership,
            "providers": [p.value for p in AIProvider],
            "models_by_provider": models_by_provider,
            "default_models": {p.value: m for p, m in DEFAULT_MODELS.items()},
            "agent": None,  # New agent
        },
    )


@router.post("/agents")
async def create_agent(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    name: Annotated[str, Form()],
    display_name: Annotated[str, Form()],
    provider: Annotated[str, Form()],
    api_key: Annotated[str, Form()],
    model: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    system_prompt: Annotated[str | None, Form()] = None,
    temperature: Annotated[float, Form()] = 0.7,
    max_tokens: Annotated[int, Form()] = 4096,
    can_read_channels: Annotated[bool, Form()] = False,
    can_read_artifacts: Annotated[bool, Form()] = False,
    can_read_notes: Annotated[bool, Form()] = False,
    workspace_id: Annotated[int | None, Form()] = None,
):
    """Create a new AI agent."""
    service = AIAgentService(db)
    
    scope = AIAgentScope.USER
    ws_id = None
    
    if workspace_id:
        workspace, membership = await get_workspace_membership(workspace_id, user.id, db)
        if membership.role not in [MembershipRole.ADMIN, MembershipRole.OWNER]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
        scope = AIAgentScope.WORKSPACE
        ws_id = workspace_id
    
    agent = await service.create_agent(
        name=name,
        display_name=display_name,
        provider=AIProvider(provider),
        api_key=api_key,
        model=model,
        scope=scope,
        workspace_id=ws_id,
        owner_id=user.id if scope == AIAgentScope.USER else None,
        description=description,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        can_read_channels=can_read_channels,
        can_read_artifacts=can_read_artifacts,
        can_read_notes=can_read_notes,
    )
    
    # Redirect to agent detail or list
    if workspace_id:
        return RedirectResponse(
            f"/workspaces/{workspace_id}/settings/ai",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse("/ai/agents", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/agents/{agent_id}", response_class=HTMLResponse)
async def view_agent(
    request: Request,
    agent_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """View an AI agent's details."""
    service = AIAgentService(db)
    agent = await service.get_agent(agent_id)
    
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    
    # Check access
    membership = None
    if agent.workspace_id:
        result = await db.execute(
            select(Membership).where(
                Membership.workspace_id == agent.workspace_id,
                Membership.user_id == user.id,
            )
        )
        membership = result.scalar_one_or_none()
    
    if not check_agent_access(agent, user.id, membership):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    can_admin = check_agent_admin(agent, user.id, membership)
    
    return templates.TemplateResponse(
        "ai/agent_detail.html",
        {
            "request": request,
            "user": user,
            "agent": agent,
            "can_admin": can_admin,
        },
    )


@router.get("/agents/{agent_id}/edit", response_class=HTMLResponse)
async def edit_agent_form(
    request: Request,
    agent_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Show form to edit an AI agent."""
    service = AIAgentService(db)
    agent = await service.get_agent(agent_id)
    
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    
    # Check admin access
    membership = None
    workspace = None
    if agent.workspace_id:
        workspace, membership = await get_workspace_membership(agent.workspace_id, user.id, db)
    
    if not check_agent_admin(agent, user.id, membership):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    models_by_provider = {
        provider.value: get_available_models(provider)
        for provider in AIProvider
    }
    
    return templates.TemplateResponse(
        "ai/agent_form.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "membership": membership,
            "agent": agent,
            "providers": [p.value for p in AIProvider],
            "models_by_provider": models_by_provider,
            "default_models": {p.value: m for p, m in DEFAULT_MODELS.items()},
        },
    )


@router.post("/agents/{agent_id}")
async def update_agent(
    request: Request,
    agent_id: int,
    user: CurrentUser,
    db: DBSession,
    name: Annotated[str, Form()],
    display_name: Annotated[str, Form()],
    provider: Annotated[str, Form()],
    model: Annotated[str, Form()],
    api_key: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    system_prompt: Annotated[str | None, Form()] = None,
    temperature: Annotated[float, Form()] = 0.7,
    max_tokens: Annotated[int, Form()] = 4096,
    can_read_channels: Annotated[bool, Form()] = False,
    can_read_artifacts: Annotated[bool, Form()] = False,
    can_read_notes: Annotated[bool, Form()] = False,
):
    """Update an AI agent."""
    service = AIAgentService(db)
    agent = await service.get_agent(agent_id)
    
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    
    # Check admin access
    membership = None
    if agent.workspace_id:
        _, membership = await get_workspace_membership(agent.workspace_id, user.id, db)
    
    if not check_agent_admin(agent, user.id, membership):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    updates = {
        "name": name,
        "display_name": display_name,
        "provider": AIProvider(provider),
        "model": model,
        "description": description,
        "system_prompt": system_prompt,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "can_read_channels": can_read_channels,
        "can_read_artifacts": can_read_artifacts,
        "can_read_notes": can_read_notes,
    }
    
    # Only update API key if provided (allows keeping existing)
    if api_key:
        updates["api_key"] = api_key
    
    await service.update_agent(agent_id, **updates)
    
    if agent.workspace_id:
        return RedirectResponse(
            f"/workspaces/{agent.workspace_id}/settings/ai",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse("/ai/agents", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/agents/{agent_id}/delete")
async def delete_agent(
    request: Request,
    agent_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Delete an AI agent."""
    service = AIAgentService(db)
    agent = await service.get_agent(agent_id)
    
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    
    # Check admin access
    membership = None
    workspace_id = agent.workspace_id
    if workspace_id:
        _, membership = await get_workspace_membership(workspace_id, user.id, db)
    
    if not check_agent_admin(agent, user.id, membership):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    await service.delete_agent(agent_id)
    
    if workspace_id:
        return RedirectResponse(
            f"/workspaces/{workspace_id}/settings/ai",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse("/ai/agents", status_code=status.HTTP_303_SEE_OTHER)


# =============================================================================
# Conversations
# =============================================================================

@router.get("/conversations", response_class=HTMLResponse)
async def list_conversations(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    agent_id: int | None = None,
):
    """List user's AI conversations."""
    service = AIAgentService(db)
    conversations = await service.get_user_conversations(user.id, agent_id)
    
    # Get available agents for starting new conversations
    agents = await service.get_user_agents(user.id)
    
    return templates.TemplateResponse(
        "ai/conversations_list.html",
        {
            "request": request,
            "user": user,
            "conversations": conversations,
            "agents": agents,
            "selected_agent_id": agent_id,
        },
    )


@router.get("/chat/{agent_id}", response_class=HTMLResponse)
async def chat_with_agent(
    request: Request,
    agent_id: int,
    user: CurrentUser,
    db: DBSession,
    workspace_id: int | None = None,
    channel_id: int | None = None,
):
    """Start or continue a chat with an AI agent."""
    service = AIAgentService(db)
    agent = await service.get_agent(agent_id)
    
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    
    # Check access
    membership = None
    workspace = None
    if agent.workspace_id:
        workspace, membership = await get_workspace_membership(agent.workspace_id, user.id, db)
    elif workspace_id:
        workspace, membership = await get_workspace_membership(workspace_id, user.id, db)
    
    if not check_agent_access(agent, user.id, membership):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    # Get or create conversation
    conversation = await service.get_or_create_conversation(
        agent_id=agent_id,
        user_id=user.id,
        channel_id=channel_id,
    )
    
    # Get message history
    messages = await service.get_conversation_messages(conversation.id)
    
    return templates.TemplateResponse(
        "ai/chat.html",
        {
            "request": request,
            "user": user,
            "agent": agent,
            "conversation": conversation,
            "messages": messages,
            "workspace": workspace,
            "workspace_id": workspace_id or agent.workspace_id,
            "channel_id": channel_id,
        },
    )


@router.post("/chat/{agent_id}/send")
async def send_chat_message(
    request: Request,
    agent_id: int,
    user: CurrentUser,
    db: DBSession,
    message: Annotated[str, Form()],
    conversation_id: Annotated[int | None, Form()] = None,
    workspace_id: Annotated[int | None, Form()] = None,
    channel_id: Annotated[int | None, Form()] = None,
):
    """Send a message to an AI agent."""
    service = AIAgentService(db)
    agent = await service.get_agent(agent_id)
    
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    
    # Check access
    membership = None
    if agent.workspace_id:
        _, membership = await get_workspace_membership(agent.workspace_id, user.id, db)
    elif workspace_id:
        _, membership = await get_workspace_membership(workspace_id, user.id, db)
    
    if not check_agent_access(agent, user.id, membership):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    # Get or create conversation
    if conversation_id:
        conversation = await service.get_conversation(conversation_id)
        if not conversation or conversation.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    else:
        conversation = await service.get_or_create_conversation(
            agent_id=agent_id,
            user_id=user.id,
            channel_id=channel_id,
        )
    
    # Send message and get response
    ws_id = workspace_id or agent.workspace_id
    ai_response = await service.send_message(
        conversation_id=conversation.id,
        user_message=message,
        workspace_id=ws_id,
        channel_id=channel_id,
    )
    
    # For HTMX requests, return the new messages
    if request.headers.get("HX-Request"):
        # Get latest messages (user message + AI response)
        messages = await service.get_conversation_messages(conversation.id, limit=2)
        
        return templates.TemplateResponse(
            "ai/partials/chat_messages.html",
            {
                "request": request,
                "messages": messages,
                "agent": agent,
            },
        )
    
    # Otherwise redirect back to chat
    redirect_url = f"/ai/chat/{agent_id}?workspace_id={ws_id}" if ws_id else f"/ai/chat/{agent_id}"
    return RedirectResponse(redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/conversations/{conversation_id}/archive")
async def archive_conversation(
    request: Request,
    conversation_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Archive a conversation."""
    service = AIAgentService(db)
    conversation = await service.get_conversation(conversation_id)
    
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    
    await service.archive_conversation(conversation_id)
    
    return RedirectResponse("/ai/conversations", status_code=status.HTTP_303_SEE_OTHER)


# =============================================================================
# Workspace AI Settings
# =============================================================================

@router.get("/workspace/{workspace_id}/agents", response_class=HTMLResponse)
async def list_workspace_agents(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """List AI agents for a workspace (admin view)."""
    workspace, membership = await get_workspace_membership(workspace_id, user.id, db)
    
    if membership.role not in [MembershipRole.ADMIN, MembershipRole.OWNER]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    service = AIAgentService(db)
    agents = await service.get_workspace_agents(workspace_id)
    
    return templates.TemplateResponse(
        "ai/workspace_agents.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "membership": membership,
            "agents": agents,
            "providers": [p.value for p in AIProvider],
        },
    )


# =============================================================================
# Channel AI Membership
# =============================================================================

@router.post("/channels/{channel_id}/agents/{agent_id}/add")
async def add_agent_to_channel(
    request: Request,
    channel_id: int,
    agent_id: int,
    user: CurrentUser,
    db: DBSession,
    respond_to_mentions: Annotated[bool, Form()] = True,
    respond_to_all: Annotated[bool, Form()] = False,
):
    """Add an AI agent to a channel."""
    # Get channel
    result = await db.execute(
        select(Channel).where(Channel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    
    # Check membership
    workspace, membership = await get_workspace_membership(channel.workspace_id, user.id, db)
    
    # Get agent
    service = AIAgentService(db)
    agent = await service.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    
    # Verify agent belongs to workspace or user
    if not check_agent_access(agent, user.id, membership):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot add this agent")
    
    # Add to channel
    await service.add_agent_to_channel(
        agent_id=agent_id,
        channel_id=channel_id,
        added_by_id=user.id,
        respond_to_mentions=respond_to_mentions,
        respond_to_all=respond_to_all,
    )
    
    return RedirectResponse(
        f"/workspaces/{channel.workspace_id}/channels/{channel_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/channels/{channel_id}/agents/{agent_id}/remove")
async def remove_agent_from_channel(
    request: Request,
    channel_id: int,
    agent_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Remove an AI agent from a channel."""
    # Get channel
    result = await db.execute(
        select(Channel).where(Channel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    
    # Check membership
    workspace, membership = await get_workspace_membership(channel.workspace_id, user.id, db)
    
    if membership.role not in [MembershipRole.ADMIN, MembershipRole.OWNER]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    service = AIAgentService(db)
    await service.remove_agent_from_channel(agent_id, channel_id)
    
    return RedirectResponse(
        f"/workspaces/{channel.workspace_id}/channels/{channel_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
