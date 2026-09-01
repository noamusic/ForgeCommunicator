"""
Channel management router.
"""

import json
import re
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, or_, select
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import selectinload

from app.deps import CurrentUser, DBSession
from app.models.ai_agent import AIAgent, AIAgentScope
from app.models.artifact import Artifact, ArtifactType
from app.models.bridged_channel import BridgedChannel
from app.models.channel import Channel
from app.models.membership import ChannelMembership, Membership, MembershipRole, ThreadReadState
from app.models.message import Message
from app.models.product import Product
from app.models.reaction import MessageReaction
from app.models.user import User
from app.models.workspace import Workspace
from app.templates_config import templates

router = APIRouter(prefix="/workspaces/{workspace_id}/channels", tags=["channels"])


async def get_sidebar_bridge_metadata(channels: list[Channel], db) -> dict:
    """Return bridge-derived grouping data for the channel sidebar."""
    channel_ids = [ch.id for ch in channels]
    slack_channel_ids = set()
    slack_channel_names = {}

    if channel_ids:
        result = await db.execute(
            select(BridgedChannel).where(
                BridgedChannel.channel_id.in_(channel_ids),
                BridgedChannel.platform == "slack",
                BridgedChannel.is_active == True,
            )
        )
        for bridge in result.scalars().all():
            slack_channel_ids.add(bridge.channel_id)
            slack_channel_names[bridge.channel_id] = bridge.external_channel_name

    # Backwards-compatible fallback for older synced channel names.
    for ch in channels:
        if ch.name.upper().startswith("SLACK:"):
            slack_channel_ids.add(ch.id)
            slack_channel_names.setdefault(ch.id, ch.name[6:].strip())

    return {
        "slack_channel_ids": slack_channel_ids,
        "slack_channel_names": slack_channel_names,
    }


async def get_workspace_and_membership(
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


@router.get("", response_class=HTMLResponse)
async def list_channels(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
    current_channel_id: int | None = None,
):
    """List channels in workspace.

    When called as an HTMX request (sidebar refresh), returns just the sidebar
    partial with accurate unread counts and the active channel highlighted.
    """
    workspace, membership = await get_workspace_and_membership(workspace_id, user.id, db)

    # Get public channels and private channels user is a member of
    result = await db.execute(
        select(Channel)
        .where(
            Channel.workspace_id == workspace_id,
            Channel.is_archived == False,
            or_(
                Channel.is_private == False,
                and_(
                    Channel.is_private == True,
                    Channel.id.in_(
                        select(ChannelMembership.channel_id)
                        .where(ChannelMembership.user_id == user.id)
                    )
                )
            )
        )
        .order_by(Channel.name)
    )
    channels = result.scalars().all()
    sidebar_bridge_metadata = await get_sidebar_bridge_metadata(channels, db)

    # Get products for grouping
    result = await db.execute(
        select(Product).where(Product.workspace_id == workspace_id, Product.is_active == True)
    )
    products = result.scalars().all()

    if request.headers.get("HX-Request"):
        # Compute real unread counts for the sidebar refresh
        from sqlalchemy import func as sqlfunc

        ch_ids = [ch.id for ch in channels]
        unread_channels: dict[int, int] = {}

        if ch_ids:
            result = await db.execute(
                select(ChannelMembership)
                .where(ChannelMembership.user_id == user.id, ChannelMembership.channel_id.in_(ch_ids))
            )
            visited: dict[int, int | None] = {}
            visited_set: set[int] = set()
            for cm in result.scalars().all():
                visited[cm.channel_id] = cm.last_read_message_id
                visited_set.add(cm.channel_id)

            for ch_id in ch_ids:
                # Current channel: always 0 (user is actively viewing it)
                if ch_id == current_channel_id:
                    unread_channels[ch_id] = 0
                    continue

                # A membership with last_read_message_id=None means "never read
                # anything here" — same as no membership: count all messages
                # from others. This matches the mobile API semantics.
                last_read_id = visited.get(ch_id)
                unread_filters = [
                    Message.channel_id == ch_id,
                    Message.deleted_at == None,
                    Message.parent_id == None,
                    Message.user_id.is_distinct_from(user.id),
                ]
                if last_read_id is not None:
                    unread_filters.append(Message.id > last_read_id)
                count_result = await db.execute(
                    select(sqlfunc.count(Message.id)).where(*unread_filters)
                )
                unread_channels[ch_id] = count_result.scalar() or 0

        return templates.TemplateResponse(
            "partials/channel_sidebar.html",
            {
                "request": request,
                "user": user,
                "workspace": workspace,
                "channels": channels,
                "products": products,
                "current_channel_id": current_channel_id,
                "unread_channels": unread_channels,
                **sidebar_bridge_metadata,
            },
        )

    return templates.TemplateResponse(
        "channels/list.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "channels": channels,
            "products": products,
            **sidebar_bridge_metadata,
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def new_channel_form(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Render new channel form."""
    workspace, membership = await get_workspace_and_membership(workspace_id, user.id, db)
    
    # Get products for linking
    result = await db.execute(
        select(Product).where(Product.workspace_id == workspace_id, Product.is_active == True)
    )
    products = result.scalars().all()
    
    return templates.TemplateResponse(
        "channels/new.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "products": products,
        },
    )


@router.post("/new")
async def create_channel(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
    name: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    is_private: Annotated[bool, Form()] = False,
    product_id: Annotated[int | None, Form()] = None,
):
    """Create a new channel."""
    workspace, membership = await get_workspace_and_membership(workspace_id, user.id, db)
    
    # Validate name (lowercase, alphanumeric and hyphens only)
    channel_name = name.lower().strip()
    channel_name = re.sub(r'[^a-z0-9-]', '-', channel_name)
    channel_name = re.sub(r'-+', '-', channel_name).strip('-')[:80]
    
    if not channel_name:
        if request.headers.get("HX-Request"):
            return HTMLResponse('<div class="text-red-500">Invalid channel name</div>', status_code=400)
        raise HTTPException(status_code=400, detail="Invalid channel name")
    
    # Check if channel name exists in workspace
    result = await db.execute(
        select(Channel).where(
            Channel.workspace_id == workspace_id,
            Channel.name == channel_name,
        )
    )
    if result.scalar_one_or_none():
        if request.headers.get("HX-Request"):
            return HTMLResponse('<div class="text-red-500">Channel name already exists</div>', status_code=400)
        raise HTTPException(status_code=400, detail="Channel name already exists")
    
    # Create channel
    channel = Channel(
        workspace_id=workspace_id,
        name=channel_name,
        description=description.strip() if description else None,
        is_private=is_private,
        product_id=product_id if product_id else None,
    )
    db.add(channel)
    await db.flush()
    
    # Add creator to private channel
    if is_private:
        channel_membership = ChannelMembership(
            channel_id=channel.id,
            user_id=user.id,
        )
        db.add(channel_membership)
    
    await db.commit()
    
    if request.headers.get("HX-Request"):
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = f"/workspaces/{workspace_id}/channels/{channel.id}"
        return response
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/channels/{channel.id}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/{channel_id}", response_class=HTMLResponse)
async def channel_view(
    request: Request,
    workspace_id: int,
    channel_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """View a channel with messages."""
    workspace, membership = await get_workspace_and_membership(workspace_id, user.id, db)
    
    # Sync Google Calendar status if linked (async, don't block)
    # Use background-safe version that creates its own db session
    if user.has_google_linked:
        try:
            from app.services.google_calendar import sync_user_calendar_status_background
            import asyncio
            # Run in background with its own session - don't await
            asyncio.create_task(sync_user_calendar_status_background(user.id))
        except Exception:
            pass  # Calendar sync failures shouldn't block channel view
    
    # Get channel
    result = await db.execute(
        select(Channel)
        .where(Channel.id == channel_id, Channel.workspace_id == workspace_id)
        .options(selectinload(Channel.product), selectinload(Channel.memberships))
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    
    # For DM channels, get the other user's profile
    dm_partner = None
    if channel.is_dm and channel.memberships:
        # Get user IDs of all DM participants except current user
        partner_ids = [m.user_id for m in channel.memberships if m.user_id != user.id]
        if partner_ids:
            result = await db.execute(
                select(User).where(User.id == partner_ids[0])
            )
            dm_partner = result.scalar_one_or_none()
    
    # Check access to private channel
    if channel.is_private:
        result = await db.execute(
            select(ChannelMembership).where(
                ChannelMembership.channel_id == channel_id,
                ChannelMembership.user_id == user.id,
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this channel")
    
    # Get channels for sidebar
    result = await db.execute(
        select(Channel)
        .where(
            Channel.workspace_id == workspace_id,
            Channel.is_archived == False,
            or_(
                Channel.is_private == False,
                and_(
                    Channel.is_private == True,
                    Channel.id.in_(
                        select(ChannelMembership.channel_id)
                        .where(ChannelMembership.user_id == user.id)
                    )
                )
            )
        )
        .order_by(Channel.name)
    )
    channels = result.scalars().all()
    sidebar_bridge_metadata = await get_sidebar_bridge_metadata(channels, db)
    
    # Get products
    result = await db.execute(
        select(Product).where(Product.workspace_id == workspace_id, Product.is_active == True)
    )
    products = result.scalars().all()
    
    # Get recent messages (exclude thread replies)
    result = await db.execute(
        select(Message)
        .where(
            Message.channel_id == channel_id,
            Message.deleted_at == None,
            Message.parent_id == None,  # Only top-level messages
        )
        .options(
            selectinload(Message.user),
            selectinload(Message.attachments),
            selectinload(Message.reactions).selectinload(MessageReaction.user),
        )
        .order_by(Message.created_at.desc())
        .limit(50)
    )
    messages = list(reversed(result.scalars().all()))
    
    # Update last read message for this channel.
    # Always upsert the membership read-pointer so even an empty-channel visit
    # establishes a baseline and future messages don't pile up as unread.
    # Use the channel-wide max message id (including thread replies) so clients
    # that count replies toward unread (mobile) also see the channel as read.
    from sqlalchemy import func as sqlfunc
    last_id_result = await db.execute(
        select(sqlfunc.max(Message.id)).where(
            Message.channel_id == channel_id,
            Message.deleted_at == None,
        )
    )
    last_message_id = last_id_result.scalar_one_or_none()
    result = await db.execute(
        select(ChannelMembership).where(
            ChannelMembership.channel_id == channel_id,
            ChannelMembership.user_id == user.id,
        )
    )
    channel_membership = result.scalar_one_or_none()
    if channel_membership:
        if last_message_id is not None:
            channel_membership.last_read_message_id = last_message_id
    elif not channel.is_private:
        # For public channels, create membership record for read tracking.
        # last_read_message_id=None is valid: it means "user has visited but
        # there are no messages yet; don't count pre-existing messages as unread."
        channel_membership = ChannelMembership(
            channel_id=channel_id,
            user_id=user.id,
            last_read_message_id=last_message_id,
        )
        db.add(channel_membership)
    await db.commit()
    
    # Get unread counts for all channels
    # First get user's channel memberships with last_read_message_id
    result = await db.execute(
        select(ChannelMembership)
        .where(ChannelMembership.user_id == user.id)
    )
    user_channel_memberships = {}
    visited_channel_ids: set[int] = set()
    for cm in result.scalars().all():
        user_channel_memberships[cm.channel_id] = cm.last_read_message_id
        visited_channel_ids.add(cm.channel_id)

    # Get unread message counts for each channel
    from sqlalchemy import func as sqlfunc

    # Build unread counts - count messages newer than last_read_message_id.
    # last_read_message_id=None (or no membership row) means "never read
    # anything here": count all messages from others. Matches mobile API.
    unread_channels = {}
    channel_ids = [ch.id for ch in channels]

    if channel_ids:
        for ch_id in channel_ids:
            last_read_id = user_channel_memberships.get(ch_id)
            unread_filters = [
                Message.channel_id == ch_id,
                Message.deleted_at == None,
                Message.parent_id == None,
                Message.user_id.is_distinct_from(user.id),
            ]
            if last_read_id is not None:
                unread_filters.append(Message.id > last_read_id)
            count_result = await db.execute(
                select(sqlfunc.count(Message.id)).where(*unread_filters)
            )
            unread_channels[ch_id] = count_result.scalar() or 0
    
    # Calculate unread thread reply counts for messages in this channel
    unread_thread_counts = {}
    messages_with_threads = [m for m in messages if m.thread_reply_count and m.thread_reply_count > 0]
    if messages_with_threads:
        thread_message_ids = [m.id for m in messages_with_threads]
        
        # Get user's thread read states
        result = await db.execute(
            select(ThreadReadState).where(
                ThreadReadState.user_id == user.id,
                ThreadReadState.parent_message_id.in_(thread_message_ids),
            )
        )
        thread_read_states = {trs.parent_message_id: trs.last_read_reply_id for trs in result.scalars().all()}
        
        # For each thread, count unread replies
        for msg in messages_with_threads:
            last_read_id = thread_read_states.get(msg.id)
            if last_read_id is None:
                # Never opened thread - all replies are unread (unless it's the user's own replies)
                count_result = await db.execute(
                    select(sqlfunc.count(Message.id))
                    .where(
                        Message.parent_id == msg.id,
                        Message.deleted_at == None,
                        Message.user_id.is_distinct_from(user.id),  # Don't count own replies as unread
                    )
                )
                unread_thread_counts[msg.id] = count_result.scalar() or 0
            else:
                # Count replies after last read
                count_result = await db.execute(
                    select(sqlfunc.count(Message.id))
                    .where(
                        Message.parent_id == msg.id,
                        Message.deleted_at == None,
                        Message.id > last_read_id,
                        Message.user_id.is_distinct_from(user.id),  # Don't count own replies as unread
                    )
                )
                unread_thread_counts[msg.id] = count_result.scalar() or 0
    
    # Get recent artifacts for channel
    result = await db.execute(
        select(Artifact)
        .where(Artifact.channel_id == channel_id)
        .order_by(Artifact.created_at.desc())
        .limit(10)
    )
    artifacts = result.scalars().all()
    
    # Get workspace members for @mentions
    result = await db.execute(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.workspace_id == workspace_id)
        .order_by(User.display_name)
    )
    members = result.scalars().all()
    
    # Get AI agents for @mentions (workspace agents with respond to mentions enabled)
    result = await db.execute(
        select(AIAgent)
        .where(
            AIAgent.workspace_id == workspace_id,
            AIAgent.is_active == True,
        )
        .order_by(AIAgent.display_name)
    )
    ai_agents = result.scalars().all()
    
    # Filter to agents that can respond to mentions
    mentionable_agents = [a for a in ai_agents if a.capabilities and a.capabilities.get('can_respond_mentions')]
    
    import json
    members_list = [
        {"id": m.id, "display_name": m.display_name, "email": m.email, "is_ai": False}
        for m in members
    ]
    # Add AI agents with special prefix
    for agent in mentionable_agents:
        members_list.append({
            "id": f"ai:{agent.id}",
            "display_name": f"🤖 {agent.display_name}",
            "email": f"AI Agent • {agent.provider.value.title()}",
            "is_ai": True,
            "agent_id": agent.id,
        })
    members_json = json.dumps(members_list)
    
    # Get bridged channel info for external replies
    result = await db.execute(
        select(BridgedChannel)
        .where(BridgedChannel.channel_id == channel_id)
        .limit(1)
    )
    bridge = result.scalar_one_or_none()
    
    from app.settings import settings
    
    return templates.TemplateResponse(
        "channels/view.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "channel": channel,
            "channels": channels,
            "products": products,
            "messages": messages,
            "artifacts": artifacts,
            "membership": membership,
            "members_json": members_json,
            "unread_channels": unread_channels,
            "unread_thread_counts": unread_thread_counts,
            "current_channel_id": channel_id,
            "realtime_mode": settings.realtime_mode,
            "poll_interval": settings.poll_interval_seconds,
            "bridge": bridge,
            "dm_partner": dm_partner,
            **sidebar_bridge_metadata,
        },
    )


@router.post("/{channel_id}/topic")
async def set_topic(
    request: Request,
    workspace_id: int,
    channel_id: int,
    user: CurrentUser,
    db: DBSession,
    topic: Annotated[str, Form()],
):
    """Set channel topic."""
    workspace, membership = await get_workspace_and_membership(workspace_id, user.id, db)
    
    result = await db.execute(
        select(Channel).where(Channel.id == channel_id, Channel.workspace_id == workspace_id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    
    channel.topic = topic.strip()[:250] if topic.strip() else None
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            f'<span id="channel-topic" class="text-gray-500 text-sm">{channel.topic or "No topic set"}</span>'
        )
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/channels/{channel_id}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{channel_id}/join")
async def join_channel(
    request: Request,
    workspace_id: int,
    channel_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Join a channel."""
    workspace, membership = await get_workspace_and_membership(workspace_id, user.id, db)
    
    result = await db.execute(
        select(Channel).where(Channel.id == channel_id, Channel.workspace_id == workspace_id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    
    # Check if already a member
    result = await db.execute(
        select(ChannelMembership).where(
            ChannelMembership.channel_id == channel_id,
            ChannelMembership.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        # Initialize the read cursor to the latest message so pre-join
        # history doesn't show up as unread.
        latest_result = await db.execute(
            select(sqlfunc.max(Message.id)).where(
                Message.channel_id == channel_id,
                Message.deleted_at == None,
            )
        )
        channel_membership = ChannelMembership(
            channel_id=channel_id,
            user_id=user.id,
            last_read_message_id=latest_result.scalar_one_or_none(),
        )
        db.add(channel_membership)
        await db.commit()
    
    if request.headers.get("HX-Request"):
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = f"/workspaces/{workspace_id}/channels/{channel_id}"
        return response
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/channels/{channel_id}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{channel_id}/leave")
async def leave_channel(
    request: Request,
    workspace_id: int,
    channel_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Leave a channel."""
    workspace, membership = await get_workspace_and_membership(workspace_id, user.id, db)
    
    result = await db.execute(
        select(ChannelMembership).where(
            ChannelMembership.channel_id == channel_id,
            ChannelMembership.user_id == user.id,
        )
    )
    channel_membership = result.scalar_one_or_none()
    if channel_membership:
        await db.delete(channel_membership)
        await db.commit()
    
    if request.headers.get("HX-Request"):
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = f"/workspaces/{workspace_id}"
        return response
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/dm/new")
async def create_dm_channel(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
    user_ids: Annotated[str, Form()],  # JSON array of user IDs
):
    """Create a DM channel with selected users.
    
    If a DM already exists with the exact same participants, return that channel.
    """
    workspace, membership = await get_workspace_and_membership(workspace_id, user.id, db)
    
    try:
        selected_user_ids = json.loads(user_ids)
        if not isinstance(selected_user_ids, list):
            raise ValueError("Invalid format")
        selected_user_ids = [int(uid) for uid in selected_user_ids]
    except (json.JSONDecodeError, ValueError, TypeError):
        if request.headers.get("HX-Request"):
            return HTMLResponse('<div class="text-red-500">Invalid user selection</div>', status_code=400)
        raise HTTPException(status_code=400, detail="Invalid user selection")
    
    # Always include current user
    if user.id not in selected_user_ids:
        selected_user_ids.append(user.id)
    
    # Must have at least 2 participants
    if len(selected_user_ids) < 2:
        if request.headers.get("HX-Request"):
            return HTMLResponse('<div class="text-red-500">Please select at least one user</div>', status_code=400)
        raise HTTPException(status_code=400, detail="Please select at least one user")
    
    # Verify all users are members of the workspace
    result = await db.execute(
        select(Membership.user_id)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.user_id.in_(selected_user_ids)
        )
    )
    valid_user_ids = set(row[0] for row in result.fetchall())
    
    if len(valid_user_ids) != len(selected_user_ids):
        if request.headers.get("HX-Request"):
            return HTMLResponse('<div class="text-red-500">One or more users not in workspace</div>', status_code=400)
        raise HTTPException(status_code=400, detail="One or more users not in workspace")
    
    # Check for existing DM with same participants
    # Find DM channels where user is a member
    result = await db.execute(
        select(Channel)
        .where(
            Channel.workspace_id == workspace_id,
            Channel.is_dm == True,
            Channel.id.in_(
                select(ChannelMembership.channel_id)
                .where(ChannelMembership.user_id == user.id)
            )
        )
        .options(selectinload(Channel.memberships))
    )
    dm_channels = result.scalars().all()
    
    # Check if any existing DM has exact same participants
    for dm in dm_channels:
        dm_member_ids = set(m.user_id for m in dm.memberships)
        if dm_member_ids == set(selected_user_ids):
            # Found existing DM channel
            if request.headers.get("HX-Request"):
                response = HTMLResponse("")
                response.headers["HX-Redirect"] = f"/workspaces/{workspace_id}/channels/{dm.id}"
                return response
            return RedirectResponse(
                url=f"/workspaces/{workspace_id}/channels/{dm.id}",
                status_code=status.HTTP_302_FOUND,
            )
    
    # Get user display names for the channel name
    result = await db.execute(
        select(User)
        .where(User.id.in_(selected_user_ids), User.id != user.id)
    )
    other_users = result.scalars().all()
    
    if len(other_users) == 1:
        channel_name = other_users[0].display_name
    else:
        names = sorted([u.display_name for u in other_users])
        if len(names) > 3:
            channel_name = f"{', '.join(names[:2])}, +{len(names) - 2} more"
        else:
            channel_name = ", ".join(names)
    
    # Create new DM channel
    channel = Channel(
        workspace_id=workspace_id,
        name=channel_name,
        is_private=True,
        is_dm=True,
    )
    db.add(channel)
    await db.flush()
    
    # Add all participants as members
    for uid in selected_user_ids:
        channel_membership = ChannelMembership(
            channel_id=channel.id,
            user_id=uid,
        )
        db.add(channel_membership)
    
    await db.commit()
    
    if request.headers.get("HX-Request"):
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = f"/workspaces/{workspace_id}/channels/{channel.id}"
        return response
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/channels/{channel.id}",
        status_code=status.HTTP_302_FOUND,
    )


# JSON API endpoint for DM creation (used by modal)
dm_router = APIRouter(prefix="/workspaces/{workspace_id}/dm", tags=["dm"])

@dm_router.post("/create")
async def create_dm_channel_json(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Create a DM channel with selected users (JSON API).
    
    Expects JSON body: { user_ids: [1, 2], message: "optional first message" }
    Returns JSON: { channel_id: 123 } or { error: "message" }
    """
    from fastapi.responses import JSONResponse
    
    try:
        body = await request.json()
        selected_user_ids = body.get("user_ids", [])
        initial_message = body.get("message", "").strip()
    except Exception:
        return JSONResponse({"error": "Invalid request body"}, status_code=400)
    
    # Verify workspace membership
    result = await db.execute(
        select(Workspace, Membership)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(
            Workspace.id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    row = result.first()
    if not row:
        return JSONResponse({"error": "Workspace not found"}, status_code=404)
    
    workspace, membership = row
    
    # Validate user_ids
    if not isinstance(selected_user_ids, list) or len(selected_user_ids) < 1:
        return JSONResponse({"error": "Please select at least one user"}, status_code=400)
    
    selected_user_ids = [int(uid) for uid in selected_user_ids]
    
    # Always include current user
    if user.id not in selected_user_ids:
        selected_user_ids.append(user.id)
    
    # Verify all users are members of the workspace
    result = await db.execute(
        select(Membership.user_id)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.user_id.in_(selected_user_ids)
        )
    )
    valid_user_ids = set(row[0] for row in result.fetchall())
    
    if len(valid_user_ids) != len(selected_user_ids):
        return JSONResponse({"error": "One or more users not in workspace"}, status_code=400)
    
    # Check for existing DM with same participants
    result = await db.execute(
        select(Channel)
        .where(
            Channel.workspace_id == workspace_id,
            Channel.is_dm == True,
            Channel.id.in_(
                select(ChannelMembership.channel_id)
                .where(ChannelMembership.user_id == user.id)
            )
        )
        .options(selectinload(Channel.memberships))
    )
    dm_channels = result.scalars().all()
    
    # Check if any existing DM has exact same participants
    existing_channel = None
    for dm in dm_channels:
        dm_member_ids = set(m.user_id for m in dm.memberships)
        if dm_member_ids == set(selected_user_ids):
            existing_channel = dm
            break
    
    if existing_channel:
        # If there's an initial message, send it to the existing channel
        if initial_message:
            new_msg = Message(
                channel_id=existing_channel.id,
                user_id=user.id,
                body=initial_message,
            )
            db.add(new_msg)
            await db.commit()
        
        return JSONResponse({"channel_id": existing_channel.id})
    
    # Get user display names for the channel name
    result = await db.execute(
        select(User)
        .where(User.id.in_(selected_user_ids), User.id != user.id)
    )
    other_users = result.scalars().all()
    
    if len(other_users) == 1:
        channel_name = other_users[0].display_name
    else:
        names = sorted([u.display_name for u in other_users])
        if len(names) > 3:
            channel_name = f"{', '.join(names[:2])}, +{len(names) - 2} more"
        else:
            channel_name = ", ".join(names)
    
    # Create new DM channel
    channel = Channel(
        workspace_id=workspace_id,
        name=channel_name,
        is_private=True,
        is_dm=True,
    )
    db.add(channel)
    await db.flush()
    
    # Add all participants as members
    for uid in selected_user_ids:
        channel_membership = ChannelMembership(
            channel_id=channel.id,
            user_id=uid,
        )
        db.add(channel_membership)
    
    # Add initial message if provided
    if initial_message:
        new_msg = Message(
            channel_id=channel.id,
            user_id=user.id,
            body=initial_message,
        )
        db.add(new_msg)
    
    await db.commit()
    
    return JSONResponse({"channel_id": channel.id})


@router.delete("/{channel_id}")
async def delete_channel(
    request: Request,
    workspace_id: int,
    channel_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Delete a channel (admin only). Cannot delete the last channel."""
    workspace, membership = await get_workspace_and_membership(workspace_id, user.id, db)
    
    # Only admins/owners can delete channels
    if membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    # Get the channel
    result = await db.execute(
        select(Channel).where(
            Channel.id == channel_id,
            Channel.workspace_id == workspace_id,
        )
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    
    # Don't delete if it's the only non-archived channel
    result = await db.execute(
        select(Channel).where(
            Channel.workspace_id == workspace_id,
            Channel.is_archived == False,
            Channel.id != channel_id,
        )
    )
    other_channels = result.scalars().all()
    if not other_channels:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<div class="text-red-500 text-sm">Cannot delete the last channel</div>',
                status_code=400,
            )
        raise HTTPException(status_code=400, detail="Cannot delete the last channel")
    
    # Delete the channel (messages cascade delete)
    await db.delete(channel)
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse("")  # Remove the row
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/settings",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{channel_id}/archive")
async def archive_channel(
    request: Request,
    workspace_id: int,
    channel_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Archive a channel (admin only)."""
    workspace, membership = await get_workspace_and_membership(workspace_id, user.id, db)
    
    if membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    result = await db.execute(
        select(Channel).where(
            Channel.id == channel_id,
            Channel.workspace_id == workspace_id,
        )
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    
    channel.is_archived = True
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/settings",
        status_code=status.HTTP_302_FOUND,
    )
