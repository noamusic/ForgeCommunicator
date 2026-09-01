"""
Message router for sending and managing messages.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from app.deps import CurrentUser, DBSession
from app.models.ai_agent import AIAgent
from app.models.artifact import Artifact
from app.models.attachment import Attachment, get_attachment_type, is_allowed_extension
from app.models.channel import Channel
from app.models.membership import ChannelMembership, Membership, ThreadReadState
from app.models.message import Message
from app.services.slash_commands import SlashCommandParser
from app.settings import settings
from app.templates_config import templates

router = APIRouter(prefix="/workspaces/{workspace_id}/channels/{channel_id}/messages", tags=["messages"])


async def verify_channel_access(
    workspace_id: int,
    channel_id: int,
    user_id: int,
    db,
) -> Channel:
    """Verify user has access to channel."""
    # Check workspace membership
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a workspace member")
    
    # Get channel
    result = await db.execute(
        select(Channel).where(
            Channel.id == channel_id,
            Channel.workspace_id == workspace_id,
        )
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    
    # Check private channel access
    if channel.is_private:
        result = await db.execute(
            select(ChannelMembership).where(
                ChannelMembership.channel_id == channel_id,
                ChannelMembership.user_id == user_id,
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a channel member")
    
    return channel


@router.get("", response_class=HTMLResponse)
async def get_messages(
    request: Request,
    workspace_id: int,
    channel_id: int,
    user: CurrentUser,
    db: DBSession,
    after: int | None = Query(default=None),
    limit: int = Query(default=50, le=100),
):
    """Get messages for channel (supports polling). Excludes thread replies."""
    channel = await verify_channel_access(workspace_id, channel_id, user.id, db)
    
    # Build query - exclude thread replies (parent_id is null for top-level messages)
    query = (
        select(Message)
        .where(
            Message.channel_id == channel_id,
            Message.deleted_at == None,
            Message.parent_id == None,  # Only top-level messages
        )
        .options(selectinload(Message.user))
    )
    
    if after:
        query = query.where(Message.id > after)

    # Order by id (monotonic, immune to backdated external/bridged timestamps)
    # both for the LIMIT window and the final display order, so polling and
    # catch-up fetches can't return messages in a different relative order
    # than they were sent — that mismatch was causing messages to render
    # above/below where they belonged once external messages were involved.
    query = query.order_by(Message.id.desc()).limit(limit)

    result = await db.execute(query)
    messages = list(reversed(result.scalars().all()))
    
    # Return partial for HTMX polling
    if request.headers.get("HX-Request"):
        if not messages:
            return HTMLResponse("")
        
        return templates.TemplateResponse(
            "partials/message_list.html",
            {
                "request": request,
                "messages": messages,
                "user": user,
                "workspace_id": workspace_id,
                "channel_id": channel_id,
                "append": after is not None,
                "unread_thread_counts": {},  # New messages have no thread replies yet
            },
        )
    
    return templates.TemplateResponse(
        "partials/message_list.html",
        {
            "request": request,
            "messages": messages,
            "user": user,
            "workspace_id": workspace_id,
            "channel_id": channel_id,
            "append": False,
            "unread_thread_counts": {},  # New messages have no thread replies yet
        },
    )


@router.post("/read", status_code=204)
async def mark_channel_read(
    workspace_id: int,
    channel_id: int,
    user: CurrentUser,
    db: DBSession,
) -> None:
    """Mark all messages in a channel as read for the current user.

    Updates last_read_message_id to the latest message so the unread badge clears.
    Called client-side whenever the user is actively viewing a channel and new messages arrive.
    """
    channel = await verify_channel_access(workspace_id, channel_id, user.id, db)

    # Find the latest message ID in this channel
    result = await db.execute(
        select(Message.id)
        .where(Message.channel_id == channel_id, Message.deleted_at == None)
        .order_by(Message.id.desc())
        .limit(1)
    )
    latest_message_id = result.scalar_one_or_none()

    if latest_message_id is None:
        return

    # Update or create the membership read pointer
    result = await db.execute(
        select(ChannelMembership).where(
            ChannelMembership.channel_id == channel_id,
            ChannelMembership.user_id == user.id,
        )
    )
    channel_membership = result.scalar_one_or_none()

    if channel_membership:
        if (channel_membership.last_read_message_id or 0) < latest_message_id:
            channel_membership.last_read_message_id = latest_message_id
            await db.commit()
    elif not channel.is_private:
        db.add(
            ChannelMembership(
                channel_id=channel_id,
                user_id=user.id,
                last_read_message_id=latest_message_id,
            )
        )
        await db.commit()


@router.post("", response_class=HTMLResponse)
async def send_message(
    request: Request,
    workspace_id: int,
    channel_id: int,
    user: CurrentUser,
    db: DBSession,
    body: Annotated[str, Form()],
    parent_id: int | None = Query(default=None),
):
    """Send a message to channel. If parent_id is provided, this is a thread reply."""
    channel = await verify_channel_access(workspace_id, channel_id, user.id, db)
    
    body = body.strip()
    if not body:
        if request.headers.get("HX-Request"):
            return HTMLResponse("")
        raise HTTPException(status_code=400, detail="Message body required")
    
    # Check for slash commands
    parsed = SlashCommandParser.parse(body)
    
    if parsed:
        if not parsed.is_valid:
            # Return error for invalid command
            if request.headers.get("HX-Request"):
                return HTMLResponse(
                    f'<div class="text-red-500 text-sm p-2">{parsed.error}</div>',
                    status_code=400,
                )
            raise HTTPException(status_code=400, detail=parsed.error)
        
        # Handle artifact creation commands
        artifact_type = SlashCommandParser.get_artifact_type(parsed.command)
        if artifact_type:
            from app.models.artifact import Artifact
            
            artifact = Artifact(
                workspace_id=workspace_id,
                channel_id=channel_id,
                product_id=channel.product_id,
                type=artifact_type,
                title=parsed.title,
                body=parsed.body,
                status=Artifact.get_default_status(artifact_type),
                created_by=user.id,
            )
            
            # Task-specific fields
            if parsed.command == 'task':
                if parsed.due_date:
                    artifact.due_date = parsed.due_date
                if parsed.assignee:
                    # Look up assignee by username/display_name
                    from app.models.user import User
                    result = await db.execute(
                        select(User).where(User.display_name.ilike(f"%{parsed.assignee}%"))
                    )
                    assignee = result.scalar_one_or_none()
                    if assignee:
                        artifact.assignee_user_id = assignee.id
            
            db.add(artifact)
            await db.commit()
            
            # Create a system message about the artifact
            system_message = f"Created {artifact_type.value}: **{parsed.title}**"
            message = Message(
                channel_id=channel_id,
                user_id=user.id,
                body=system_message,
            )
            db.add(message)
            await db.commit()
            await db.refresh(message)
            
            if request.headers.get("HX-Request"):
                return templates.TemplateResponse(
                    "partials/message_item.html",
                    {
                        "request": request,
                        "message": message,
                        "user": user,
                        "workspace_id": workspace_id,
                        "channel_id": channel_id,
                    },
                )
        
        # Handle channel commands
        elif parsed.command == 'join':
            # Redirect to join channel
            if request.headers.get("HX-Request"):
                response = HTMLResponse("")
                response.headers["HX-Redirect"] = f"/workspaces/{workspace_id}/channels?join={parsed.channel_name}"
                return response
        
        elif parsed.command == 'leave':
            if request.headers.get("HX-Request"):
                response = HTMLResponse("")
                response.headers["HX-Redirect"] = f"/workspaces/{workspace_id}/channels/{channel_id}/leave"
                return response
        
        elif parsed.command == 'topic':
            channel.topic = parsed.topic
            await db.commit()
            if request.headers.get("HX-Request"):
                return HTMLResponse(
                    '<div class="text-green-500 text-sm p-2">Topic updated</div>'
                )
    
    # Regular message (or thread reply)
    message = Message(
        channel_id=channel_id,
        user_id=user.id,
        body=body,
        parent_id=parent_id,
    )
    db.add(message)
    
    # Update parent's reply count if this is a thread reply
    if parent_id:
        result = await db.execute(
            select(Message).where(Message.id == parent_id)
        )
        parent_message = result.scalar_one_or_none()
        if parent_message:
            parent_message.thread_reply_count = (parent_message.thread_reply_count or 0) + 1
    
    await db.commit()
    await db.refresh(message)
    
    # Load user for template
    result = await db.execute(
        select(Message)
        .where(Message.id == message.id)
        .options(selectinload(Message.user))
    )
    message = result.scalar_one()
    
    # Send push notifications in background (fire-and-forget) to avoid blocking the response
    import asyncio

    async def _send_push_notifications():
        """Background task for push notifications — uses its own DB session."""
        from app.db import async_session_maker
        from app.services.push import push_service
        from app.models.user import User
        from app.models.workspace import Workspace
        import re
        import logging
        push_logger = logging.getLogger(__name__ + ".push_bg")

        try:
            async with async_session_maker() as bg_db:
                # Check if this is a DM channel
                if channel.is_dm:
                    ws_result = await bg_db.execute(
                        select(Workspace.name).where(Workspace.id == workspace_id)
                    )
                    workspace_name = ws_result.scalar_one_or_none() or "Message"

                    result = await bg_db.execute(
                        select(ChannelMembership.user_id)
                        .where(
                            ChannelMembership.channel_id == channel_id,
                            ChannelMembership.user_id != user.id,
                        )
                    )
                    recipient_ids = [row[0] for row in result.fetchall()]

                    push_logger.info("DM by user %s to channel %s, notifying %d recipients",
                                     user.id, channel_id, len(recipient_ids))

                    for recipient_id in recipient_ids:
                        await push_service.notify_dm(
                            db=bg_db,
                            recipient_user_id=recipient_id,
                            sender_name=user.display_name,
                            workspace_id=workspace_id,
                            channel_id=channel_id,
                            message_preview=body[:100],
                            workspace_name=workspace_name,
                            message_id=message.id,
                        )

                # @mentions
                mention_pattern = re.compile(r'@(\w+(?:\s+\w+)?)', re.IGNORECASE)
                mentions = mention_pattern.findall(body)
                mentioned_user_ids: set[int] = set()

                if mentions:
                    for mention_name in mentions:
                        result = await bg_db.execute(
                            select(User)
                            .join(Membership, Membership.user_id == User.id)
                            .where(
                                Membership.workspace_id == workspace_id,
                                User.display_name.ilike(f"%{mention_name}%"),
                                User.id != user.id,
                            )
                        )
                        mentioned_user = result.scalar_one_or_none()

                        if mentioned_user:
                            mentioned_user_ids.add(mentioned_user.id)
                            await push_service.notify_mention(
                                db=bg_db,
                                mentioned_user_id=mentioned_user.id,
                                sender_name=user.display_name,
                                channel_name=channel.display_name,
                                workspace_id=workspace_id,
                                channel_id=channel_id,
                                message_preview=body[:100],
                                message_id=message.id,
                            )

                # AI agent mentions
                for mention_name in mentions:
                    result = await bg_db.execute(
                        select(AIAgent)
                        .where(
                            AIAgent.workspace_id == workspace_id,
                            AIAgent.is_active == True,
                            AIAgent.display_name.ilike(f"%{mention_name}%"),
                        )
                    )
                    mentioned_agent = result.scalar_one_or_none()

                    if mentioned_agent and mentioned_agent.capabilities and mentioned_agent.capabilities.get('can_respond_mentions'):
                        asyncio.create_task(
                            _generate_ai_mention_response(
                                db_factory=bg_db._session_factory if hasattr(bg_db, '_session_factory') else None,
                                agent_id=mentioned_agent.id,
                                channel_id=channel_id,
                                workspace_id=workspace_id,
                                message_id=message.id,
                                user_message=body,
                                user_name=user.display_name,
                            )
                        )

                # Notify members who opted in to all channel messages (non-DM top-level only)
                if not channel.is_dm and not parent_id:
                    result = await bg_db.execute(
                        select(Membership).where(
                            Membership.workspace_id == workspace_id,
                            Membership.notify_all_messages == True,  # noqa: E712
                            Membership.user_id != user.id,
                        )
                    )
                    all_msg_members = result.scalars().all()

                    for member in all_msg_members:
                        if member.user_id in mentioned_user_ids:
                            continue
                        await push_service.notify_channel_message(
                            db=bg_db,
                            user_id=member.user_id,
                            sender_name=user.display_name,
                            channel_name=channel.display_name,
                            workspace_id=workspace_id,
                            channel_id=channel_id,
                            message_preview=body[:100],
                            message_id=message.id,
                        )
        except Exception as e:
            logging.getLogger(__name__).error(f"Background push notification error: {e}")

    asyncio.create_task(_send_push_notifications())
    
    # Broadcast new message via WebSocket to other users in the channel
    try:
        from app.routers.realtime import broadcast_new_message
        from io import StringIO
        
        # Render the message HTML for WebSocket broadcast
        message_html = templates.TemplateResponse(
            "partials/message_item.html" if not parent_id else "partials/thread_reply_item.html",
            {
                "request": request,
                "message": message if not parent_id else None,
                "reply": message if parent_id else None,
                "user": user,
                "workspace_id": workspace_id,
                "channel_id": channel_id,
            },
        ).body.decode('utf-8')
        
        await broadcast_new_message(
            channel_id=channel_id,
            message_html=message_html,
            message_id=message.id,
            user_id=user.id,
            user_name=user.display_name,
            parent_id=parent_id,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"WebSocket broadcast error: {e}")
    
    if request.headers.get("HX-Request"):
        # Different template for thread replies vs main messages
        if parent_id:
            return templates.TemplateResponse(
                "partials/thread_reply_item.html",
                {
                    "request": request,
                    "reply": message,
                    "user": user,
                    "workspace_id": workspace_id,
                    "channel_id": channel_id,
                },
            )
        return templates.TemplateResponse(
            "partials/message_item.html",
            {
                "request": request,
                "message": message,
                "user": user,
                "workspace_id": workspace_id,
                "channel_id": channel_id,
            },
        )
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/channels/{channel_id}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{message_id}/edit")
async def edit_message(
    request: Request,
    workspace_id: int,
    channel_id: int,
    message_id: int,
    user: CurrentUser,
    db: DBSession,
    body: Annotated[str, Form()],
):
    """Edit a message."""
    await verify_channel_access(workspace_id, channel_id, user.id, db)
    
    # Get message
    result = await db.execute(
        select(Message)
        .where(Message.id == message_id, Message.channel_id == channel_id)
        .options(selectinload(Message.user))
    )
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    
    # Check ownership
    if message.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot edit other user's message")
    
    # Update message
    message.body = body.strip()
    message.edited_at = datetime.now(timezone.utc)
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/message_item.html",
            {
                "request": request,
                "message": message,
                "user": user,
                "workspace_id": workspace_id,
                "channel_id": channel_id,
            },
        )
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/channels/{channel_id}",
        status_code=status.HTTP_302_FOUND,
    )


@router.delete("/{message_id}")
async def delete_message(
    request: Request,
    workspace_id: int,
    channel_id: int,
    message_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Soft delete a message."""
    await verify_channel_access(workspace_id, channel_id, user.id, db)
    
    # Get message
    result = await db.execute(
        select(Message).where(Message.id == message_id, Message.channel_id == channel_id)
    )
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    
    # Check ownership or admin
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    
    from app.models.membership import MembershipRole
    
    if message.user_id != user.id and membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete this message")
    
    # Decrement parent's reply count if this is a thread reply
    if message.parent_id:
        result = await db.execute(
            select(Message).where(Message.id == message.parent_id)
        )
        parent = result.scalar_one_or_none()
        if parent and parent.thread_reply_count > 0:
            parent.thread_reply_count -= 1
    
    # Soft delete
    message.soft_delete()
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/channels/{channel_id}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{message_id}/mark-as/{artifact_type}")
async def mark_message_as_artifact(
    request: Request,
    workspace_id: int,
    channel_id: int,
    message_id: int,
    artifact_type: str,
    user: CurrentUser,
    db: DBSession,
):
    """Create an artifact from a message (decision, feature, issue, task)."""
    from app.models.artifact import Artifact, ArtifactType
    
    # Validate artifact type
    try:
        art_type = ArtifactType(artifact_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid artifact type: {artifact_type}")
    
    await verify_channel_access(workspace_id, channel_id, user.id, db)
    
    # Get message
    result = await db.execute(
        select(Message)
        .options(selectinload(Message.user))
        .where(Message.id == message_id, Message.channel_id == channel_id)
    )
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Check if artifact already exists for this message
    result = await db.execute(
        select(Artifact).where(Artifact.source_message_id == message_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                f'<span class="text-xs text-yellow-400">Already marked as {existing.type.value}</span>'
            )
        raise HTTPException(status_code=400, detail="Message already has an artifact")
    
    # Create artifact from message
    # Use the first line or first 100 chars as title
    body_text = message.body.strip()
    first_line = body_text.split('\n')[0][:100]
    title = first_line if first_line else f"From message by {message.user.display_name if message.user else 'Unknown'}"
    
    artifact = Artifact(
        workspace_id=workspace_id,
        channel_id=channel_id,
        type=art_type,
        title=title,
        body=body_text,
        status=Artifact.get_default_status(art_type),
        created_by=user.id,
        source_message_id=message_id,
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    
    if request.headers.get("HX-Request"):
        # Return badge showing the artifact type
        badge_colors = {
            "decision": "bg-purple-600",
            "feature": "bg-blue-600",
            "issue": "bg-red-600",
            "task": "bg-green-600",
        }
        color = badge_colors.get(artifact_type, "bg-gray-600")
        return HTMLResponse(
            f'''<a href="/workspaces/{workspace_id}/artifacts/{artifact.id}" 
                   class="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded {color} text-white hover:opacity-80">
                    {artifact_type.title()}
                </a>'''
        )
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/artifacts/{artifact.id}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/{message_id}/thread", response_class=HTMLResponse)
async def get_thread(
    request: Request,
    workspace_id: int,
    channel_id: int,
    message_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Get thread content for a message."""
    await verify_channel_access(workspace_id, channel_id, user.id, db)
    
    # Get parent message
    result = await db.execute(
        select(Message)
        .where(
            Message.id == message_id,
            Message.channel_id == channel_id,
            Message.deleted_at == None,
        )
        .options(selectinload(Message.user))
    )
    parent_message = result.scalar_one_or_none()
    
    if not parent_message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    
    # Get replies
    result = await db.execute(
        select(Message)
        .where(
            Message.parent_id == message_id,
            Message.deleted_at == None,
        )
        .options(selectinload(Message.user))
        .order_by(Message.created_at.asc())
    )
    replies = result.scalars().all()
    
    # Mark thread as read - upsert ThreadReadState
    if replies:
        last_reply_id = replies[-1].id
        result = await db.execute(
            select(ThreadReadState).where(
                ThreadReadState.user_id == user.id,
                ThreadReadState.parent_message_id == message_id,
            )
        )
        thread_read = result.scalar_one_or_none()
        
        if thread_read:
            thread_read.last_read_reply_id = last_reply_id
            thread_read.updated_at = datetime.now(timezone.utc)
        else:
            thread_read = ThreadReadState(
                user_id=user.id,
                parent_message_id=message_id,
                last_read_reply_id=last_reply_id,
            )
            db.add(thread_read)
        
        await db.commit()
    
    return templates.TemplateResponse(
        "partials/thread_content.html",
        {
            "request": request,
            "parent_message": parent_message,
            "replies": replies,
            "user": user,
            "workspace_id": workspace_id,
            "channel_id": channel_id,
        },
    )


@router.get("/{message_id}", response_class=HTMLResponse)
async def get_single_message(
    request: Request,
    workspace_id: int,
    channel_id: int,
    message_id: int,
    user: CurrentUser,
    db: DBSession,
    partial: bool = Query(default=False),
):
    """Get a single message (for refreshing after thread update)."""
    await verify_channel_access(workspace_id, channel_id, user.id, db)
    
    result = await db.execute(
        select(Message)
        .where(
            Message.id == message_id,
            Message.channel_id == channel_id,
            Message.deleted_at == None,
        )
        .options(selectinload(Message.user))
    )
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    
    if partial or request.headers.get("HX-Request"):
        # Calculate unread thread count for this message
        unread_count = 0
        if message.thread_reply_count and message.thread_reply_count > 0:
            result = await db.execute(
                select(ThreadReadState).where(
                    ThreadReadState.user_id == user.id,
                    ThreadReadState.parent_message_id == message_id,
                )
            )
            thread_read = result.scalar_one_or_none()
            
            if thread_read and thread_read.last_read_reply_id:
                # Count replies after last read
                from sqlalchemy import func as sqlfunc
                count_result = await db.execute(
                    select(sqlfunc.count(Message.id))
                    .where(
                        Message.parent_id == message_id,
                        Message.deleted_at == None,
                        Message.id > thread_read.last_read_reply_id,
                        Message.user_id.is_distinct_from(user.id),
                    )
                )
                unread_count = count_result.scalar() or 0
            elif not thread_read:
                # Never opened - count all replies from others
                from sqlalchemy import func as sqlfunc
                count_result = await db.execute(
                    select(sqlfunc.count(Message.id))
                    .where(
                        Message.parent_id == message_id,
                        Message.deleted_at == None,
                        Message.user_id.is_distinct_from(user.id),
                    )
                )
                unread_count = count_result.scalar() or 0
        
        return templates.TemplateResponse(
            "partials/message_item.html",
            {
                "request": request,
                "message": message,
                "user": user,
                "workspace_id": workspace_id,
                "channel_id": channel_id,
                "unread_thread_counts": {message_id: unread_count},
            },
        )
    
    # Full page redirect to channel
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/channels/{channel_id}",
        status_code=status.HTTP_302_FOUND,
    )


# ============================================
# Message Export Endpoints
# ============================================

def format_message_to_markdown(message: Message, include_metadata: bool = True) -> str:
    """Format a single message to Markdown."""
    lines = []
    
    # Header with author and timestamp
    author = message.user.display_name if message.user else "Unknown"
    timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if message.created_at else ""
    
    if include_metadata:
        lines.append(f"**{author}** — {timestamp}")
        if message.is_edited:
            lines.append(" _(edited)_")
        lines.append("\n")
    else:
        lines.append(f"> **{author}**\n")
    
    # Message body
    body = message.body or ""
    lines.append(f"{body}\n")
    
    return "".join(lines)


def format_thread_to_markdown(
    parent_message: Message,
    replies: list[Message],
    channel_name: str,
    workspace_name: str,
) -> str:
    """Format a thread (parent + replies) to Markdown."""
    lines = []
    
    # Document header
    lines.append(f"# Thread Export\n\n")
    lines.append(f"**Workspace:** {workspace_name}  \n")
    lines.append(f"**Channel:** {channel_name}  \n")
    lines.append(f"**Exported:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  \n")
    lines.append(f"**Messages:** {1 + len(replies)}\n\n")
    lines.append("---\n\n")
    
    # Parent message
    lines.append("## Original Message\n\n")
    lines.append(format_message_to_markdown(parent_message))
    lines.append("\n")
    
    # Replies
    if replies:
        lines.append("## Replies\n\n")
        for i, reply in enumerate(replies, 1):
            lines.append(f"### Reply {i}\n\n")
            lines.append(format_message_to_markdown(reply))
            lines.append("\n")
    
    return "".join(lines)


def format_messages_to_markdown(
    messages: list[Message],
    channel_name: str,
    workspace_name: str,
    title: str = "Messages Export",
) -> str:
    """Format multiple messages to Markdown."""
    lines = []
    
    # Document header
    lines.append(f"# {title}\n\n")
    lines.append(f"**Workspace:** {workspace_name}  \n")
    lines.append(f"**Channel:** {channel_name}  \n")
    lines.append(f"**Exported:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  \n")
    lines.append(f"**Messages:** {len(messages)}\n\n")
    lines.append("---\n\n")
    
    # Messages
    for message in messages:
        lines.append(format_message_to_markdown(message))
        lines.append("\n---\n\n")
    
    return "".join(lines)


@router.get("/{message_id}/thread/export")
async def export_thread(
    request: Request,
    workspace_id: int,
    channel_id: int,
    message_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Export a thread as a Markdown document."""
    from fastapi.responses import Response
    from app.models.workspace import Workspace
    
    channel = await verify_channel_access(workspace_id, channel_id, user.id, db)
    
    # Get workspace name
    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()
    workspace_name = workspace.name if workspace else "Unknown"
    
    # Get parent message
    result = await db.execute(
        select(Message)
        .where(
            Message.id == message_id,
            Message.channel_id == channel_id,
            Message.deleted_at == None,
        )
        .options(selectinload(Message.user))
    )
    parent_message = result.scalar_one_or_none()
    
    if not parent_message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    
    # Get replies
    result = await db.execute(
        select(Message)
        .where(
            Message.parent_id == message_id,
            Message.deleted_at == None,
        )
        .options(selectinload(Message.user))
        .order_by(Message.created_at.asc())
    )
    replies = list(result.scalars().all())
    
    # Generate Markdown
    markdown_content = format_thread_to_markdown(
        parent_message=parent_message,
        replies=replies,
        channel_name=channel.display_name,
        workspace_name=workspace_name,
    )
    
    # Generate filename
    safe_channel = channel.name.replace(" ", "-").replace("/", "-")[:30]
    filename = f"thread-{safe_channel}-{message_id}.md"
    
    return Response(
        content=markdown_content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/export")
async def export_messages(
    request: Request,
    workspace_id: int,
    channel_id: int,
    user: CurrentUser,
    db: DBSession,
    message_ids: str | None = Query(default=None, description="Comma-separated message IDs to export"),
    limit: int = Query(default=50, le=200, description="Number of recent messages to export if no IDs specified"),
    include_threads: bool = Query(default=False, description="Include thread replies for each message"),
):
    """Export messages as a Markdown document.
    
    - If message_ids is provided, exports those specific messages
    - Otherwise, exports the most recent `limit` messages from the channel
    """
    from fastapi.responses import Response
    from app.models.workspace import Workspace
    
    channel = await verify_channel_access(workspace_id, channel_id, user.id, db)
    
    # Get workspace name
    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()
    workspace_name = workspace.name if workspace else "Unknown"
    
    if message_ids:
        # Export specific messages
        ids = [int(id.strip()) for id in message_ids.split(",") if id.strip().isdigit()]
        if not ids:
            raise HTTPException(status_code=400, detail="Invalid message IDs")
        
        result = await db.execute(
            select(Message)
            .where(
                Message.id.in_(ids),
                Message.channel_id == channel_id,
                Message.deleted_at == None,
            )
            .options(selectinload(Message.user))
            .order_by(Message.created_at.asc())
        )
        messages = list(result.scalars().all())
        title = f"Selected Messages from #{channel.name}"
    else:
        # Export recent messages
        result = await db.execute(
            select(Message)
            .where(
                Message.channel_id == channel_id,
                Message.deleted_at == None,
                Message.parent_id == None,  # Only top-level messages
            )
            .options(selectinload(Message.user))
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        messages = list(reversed(result.scalars().all()))
        title = f"Recent Messages from #{channel.name}"
    
    if not messages:
        raise HTTPException(status_code=404, detail="No messages found")
    
    # If include_threads, fetch replies for each message
    if include_threads:
        lines = []
        lines.append(f"# {title}\n\n")
        lines.append(f"**Workspace:** {workspace_name}  \n")
        lines.append(f"**Channel:** {channel.display_name}  \n")
        lines.append(f"**Exported:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  \n")
        lines.append(f"**Messages:** {len(messages)} (with threads)\n\n")
        lines.append("---\n\n")
        
        for msg in messages:
            lines.append(format_message_to_markdown(msg))
            
            # Get replies if this message has any
            if msg.thread_reply_count and msg.thread_reply_count > 0:
                result = await db.execute(
                    select(Message)
                    .where(
                        Message.parent_id == msg.id,
                        Message.deleted_at == None,
                    )
                    .options(selectinload(Message.user))
                    .order_by(Message.created_at.asc())
                )
                replies = result.scalars().all()
                
                if replies:
                    lines.append("\n> **Thread Replies:**\n>\n")
                    for reply in replies:
                        author = reply.user.display_name if reply.user else "Unknown"
                        timestamp = reply.created_at.strftime("%Y-%m-%d %H:%M") if reply.created_at else ""
                        body = reply.body.replace("\n", "\n> ") if reply.body else ""
                        lines.append(f"> **{author}** ({timestamp}):\n> {body}\n>\n")
            
            lines.append("\n---\n\n")
        
        markdown_content = "".join(lines)
    else:
        markdown_content = format_messages_to_markdown(
            messages=messages,
            channel_name=channel.display_name,
            workspace_name=workspace_name,
            title=title,
        )
    
    # Generate filename
    safe_channel = channel.name.replace(" ", "-").replace("/", "-")[:30]
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"messages-{safe_channel}-{date_str}.md"
    
    return Response(
        content=markdown_content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post("/upload", response_class=HTMLResponse)
async def upload_file(
    request: Request,
    workspace_id: int,
    channel_id: int,
    user: CurrentUser,
    db: DBSession,
    file: Annotated[UploadFile, File()],
    message_body: Annotated[str | None, Form()] = None,
):
    """Upload a file attachment to a channel.
    
    Creates a message with the attachment. If message_body is provided,
    it becomes the message text; otherwise creates a file-only message.
    """
    # Check if storage is enabled
    if not settings.file_storage_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="File uploads are not configured",
        )
    
    channel = await verify_channel_access(workspace_id, channel_id, user.id, db)
    
    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    
    if not is_allowed_extension(file.filename):
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed types: images, documents, text files, archives",
        )
    
    # Check file size
    file_content = await file.read()
    file_size = len(file_content)
    await file.seek(0)  # Reset for storage upload
    
    if file_size > settings.upload_max_size_bytes:
        max_mb = settings.upload_max_size_mb
        raise HTTPException(
            status_code=400,
            detail=f"File size exceeds maximum allowed ({max_mb}MB)",
        )
    
    # Upload to storage
    try:
        from app.services.storage import get_storage_service, StorageError
        
        storage = get_storage_service()
        storage_key, content_type, _ = storage.upload_file(
            file.file,
            file.filename,
            file.content_type,
            channel_id,
        )
    except StorageError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Create attachment record
    attachment = Attachment(
        filename=file.filename,
        original_filename=file.filename,
        storage_key=storage_key,
        content_type=content_type,
        file_size=file_size,
        attachment_type=get_attachment_type(file.filename).value,
        channel_id=channel_id,
        user_id=user.id,
    )
    
    # Create message with attachment
    body = message_body.strip() if message_body else f"📎 {file.filename}"
    message = Message(
        channel_id=channel_id,
        user_id=user.id,
        body=body,
    )
    db.add(message)
    await db.flush()  # Get message ID
    
    attachment.message_id = message.id
    db.add(attachment)
    await db.commit()
    await db.refresh(message)
    
    # Load message with user and attachments for template
    result = await db.execute(
        select(Message)
        .where(Message.id == message.id)
        .options(
            selectinload(Message.user),
            selectinload(Message.attachments),
        )
    )
    message = result.scalar_one()
    
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/message_item.html",
            {
                "request": request,
                "message": message,
                "user": user,
                "workspace_id": workspace_id,
                "channel_id": channel_id,
            },
        )
    
    return RedirectResponse(
        f"/workspaces/{workspace_id}/channels/{channel_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.delete("/attachments/{attachment_id}")
async def delete_attachment(
    workspace_id: int,
    channel_id: int,
    attachment_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Delete an attachment (owner or admin only)."""
    await verify_channel_access(workspace_id, channel_id, user.id, db)
    
    # Get attachment
    result = await db.execute(
        select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.channel_id == channel_id,
        )
    )
    attachment = result.scalar_one_or_none()
    
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    
    # Check ownership (or admin)
    if attachment.user_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized to delete this attachment")
    
    # Delete from storage
    try:
        from app.services.storage import get_storage_service, StorageError
        storage = get_storage_service()
        storage.delete_file(attachment.storage_key)
    except StorageError:
        pass  # Continue even if storage delete fails
    
    # Delete from database
    await db.delete(attachment)
    await db.commit()
    
    return {"success": True}


async def _generate_ai_mention_response(
    db_factory,
    agent_id: int,
    channel_id: int,
    workspace_id: int,
    message_id: int,
    user_message: str,
    user_name: str,
):
    """Background task to generate and post an AI response when mentioned."""
    import logging
    
    logger = logging.getLogger(__name__)
    logger.info(f"AI agent {agent_id} mentioned in channel {channel_id}")
    
    try:
        from app.db import get_db_async
        from app.services.ai_providers import get_provider, ChatMessage
        
        async for db in get_db_async():
            try:
                # Get the agent
                result = await db.execute(
                    select(AIAgent).where(AIAgent.id == agent_id)
                )
                agent = result.scalar_one_or_none()
                
                if not agent:
                    logger.error(f"Agent {agent_id} not found")
                    return
                
                # Get recent channel context (last 10 messages)
                result = await db.execute(
                    select(Message)
                    .where(
                        Message.channel_id == channel_id,
                        Message.parent_id.is_(None),  # Top-level messages only
                    )
                    .options(selectinload(Message.user))
                    .order_by(Message.created_at.desc())
                    .limit(10)
                )
                recent_messages = list(reversed(result.scalars().all()))
                
                # Build system prompt
                system_content = f"You are {agent.display_name}, an AI assistant in a team channel. "
                system_content += f"You were just mentioned by {user_name}. "
                system_content += "Respond helpfully and concisely to their message. "
                system_content += "Keep your response focused and relevant to the conversation. "
                system_content += "Don't add any prefix like your name - the UI will show who you are.\n\n"
                
                if agent.system_prompt:
                    system_content += f"Your instructions: {agent.system_prompt}\n\n"
                
                # Add recent conversation
                system_content += "Recent conversation:\n"
                for msg in recent_messages:
                    sender = msg.user.display_name if msg.user else "Unknown"
                    system_content += f"- {sender}: {msg.body[:200]}\n"
                
                # Build messages for AI
                messages = [
                    ChatMessage(role="system", content=system_content),
                    ChatMessage(role="user", content=f"{user_name} said: {user_message}"),
                ]
                
                # Get AI response
                provider = get_provider(
                    agent.provider,
                    agent.api_key,
                    agent.model,
                    temperature=agent.temperature or 0.7,
                    max_tokens=agent.max_tokens or 1024,
                )
                
                response = await provider.chat(messages)
                
                if response and response.content:
                    # Create a new message from the AI agent
                    # Use external_author fields to display agent info since no user
                    ai_message = Message(
                        channel_id=channel_id,
                        body=response.content,
                        user_id=None,  # No user - it's from AI
                        external_source="ai_agent",  # Mark as AI-generated
                        external_author_name=f"🤖 {agent.display_name}",
                        external_author_avatar=agent.avatar_url,
                    )
                    db.add(ai_message)
                    await db.commit()
                    await db.refresh(ai_message)
                    
                    logger.info(f"AI agent {agent_id} posted response to channel {channel_id}")
                    
                    # Broadcast via WebSocket
                    try:
                        from app.routers.realtime import broadcast_new_message
                        
                        # Build simple HTML for the AI message
                        avatar_html = f'<img src="{agent.avatar_url}" class="w-10 h-10 rounded-full object-cover" alt="{agent.display_name}">' if agent.avatar_url else '<span class="text-white text-lg">🤖</span>'
                        
                        message_html = f'''
                        <div class="flex items-start space-x-3 p-4 hover:bg-white/5 rounded-lg group" data-message-id="{ai_message.id}">
                            <div class="flex-shrink-0 w-10 h-10 rounded-full bg-gradient-to-br from-purple-500 to-blue-500 flex items-center justify-center overflow-hidden">
                                {avatar_html}
                            </div>
                            <div class="flex-1 min-w-0">
                                <div class="flex items-center space-x-2">
                                    <span class="font-medium text-white">{agent.display_name}</span>
                                    <span class="text-xs text-purple-400 px-1.5 py-0.5 bg-purple-500/20 rounded">AI</span>
                                </div>
                                <div class="text-gray-300 mt-1 whitespace-pre-wrap">{response.content}</div>
                            </div>
                        </div>
                        '''
                        
                        await broadcast_new_message(
                            channel_id=channel_id,
                            message_html=message_html,
                            message_id=ai_message.id,
                            user_id=0,  # System/AI
                            user_name=agent.display_name,
                        )
                    except Exception as ws_error:
                        logger.error(f"WebSocket broadcast error for AI message: {ws_error}")
                        
            except Exception as e:
                logger.error(f"Error generating AI response: {e}")
                import traceback
                traceback.print_exc()
            finally:
                break  # Exit the async generator
                
    except Exception as e:
        logger.error(f"Error in AI mention response: {e}")
        import traceback
        traceback.print_exc()
