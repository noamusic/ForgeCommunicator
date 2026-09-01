"""
External integrations router for Slack/Discord notifications.

Handles OAuth flows, webhook callbacks, notification settings, and channel bridging.
"""

import secrets
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps import CurrentUser, DBSession
from app.models.external_integration import (
    ExternalIntegration,
    IntegrationType,
    NotificationLog,
    NotificationSource,
)
from app.models.bridged_channel import BridgedChannel, BridgePlatform
from app.models.message import Message
from app.models.channel import Channel
from app.models.membership import ChannelMembership, Membership
from app.models.workspace import Workspace
from app.models.user import User
from app.services.slack import slack_service
from app.services.discord import discord_service
from app.settings import settings
from app.templates_config import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


# ==================== Slack OAuth ====================

@router.get("/slack/connect")
async def slack_connect(
    request: Request,
    user: CurrentUser,
):
    """Start Slack OAuth flow."""
    if not settings.slack_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack integration is not configured",
        )
    
    # Build redirect URI
    proto = request.headers.get("X-Forwarded-Proto", "https" if not settings.debug else "http")
    host = request.headers.get("X-Forwarded-Host", request.url.netloc)
    redirect_uri = f"{proto}://{host}/integrations/slack/callback"
    
    # Generate state token
    state = secrets.token_urlsafe(32)
    
    # Get authorization URL
    auth_url = slack_service.get_authorization_url(state, redirect_uri)
    
    response = RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="slack_oauth_state",
        value=state,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        max_age=600,  # 10 minutes
    )
    response.set_cookie(
        key="slack_redirect_uri",
        value=redirect_uri,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        max_age=600,
    )
    return response


@router.get("/slack/callback")
async def slack_callback(
    request: Request,
    db: DBSession,
    user: CurrentUser,
    code: str = Query(...),
    state: str = Query(...),
):
    """Handle Slack OAuth callback."""
    # Verify state
    stored_state = request.cookies.get("slack_oauth_state")
    redirect_uri = request.cookies.get("slack_redirect_uri")
    
    if not stored_state or state != stored_state:
        return RedirectResponse(
            url="/profile/integrations?error=Invalid+state",
            status_code=status.HTTP_302_FOUND,
        )
    
    # Exchange code for tokens
    token_data = await slack_service.exchange_code_for_token(code, redirect_uri)
    
    if not token_data:
        return RedirectResponse(
            url="/profile/integrations?error=Failed+to+connect+Slack",
            status_code=status.HTTP_302_FOUND,
        )
    
    # Extract user token data (authed_user contains user-level tokens)
    authed_user = token_data.get("authed_user", {})
    access_token = authed_user.get("access_token")
    
    if not access_token:
        logger.error(f"No user access token in Slack response: {token_data}")
        return RedirectResponse(
            url="/profile/integrations?error=No+access+token+received",
            status_code=status.HTTP_302_FOUND,
        )
    
    # Get or create integration record
    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.integration_type == IntegrationType.SLACK,
        )
    )
    integration = result.scalar_one_or_none()
    
    if not integration:
        integration = ExternalIntegration(
            user_id=user.id,
            integration_type=IntegrationType.SLACK,
            notification_preferences={
                "dm": True,
                "mentions": True,
                "channels": [],
            },
        )
        db.add(integration)
    
    # Update tokens and info
    integration.access_token = access_token
    integration.external_user_id = authed_user.get("id")
    integration.external_team_id = token_data.get("team", {}).get("id")
    integration.external_team_name = token_data.get("team", {}).get("name")
    integration.is_active = True
    
    await db.commit()
    
    # Clear OAuth cookies
    response = RedirectResponse(
        url="/profile/integrations?success=slack",
        status_code=status.HTTP_302_FOUND,
    )
    response.delete_cookie("slack_oauth_state")
    response.delete_cookie("slack_redirect_uri")
    return response


@router.post("/slack/disconnect")
async def slack_disconnect(
    request: Request,
    db: DBSession,
    user: CurrentUser,
):
    """Disconnect Slack integration."""
    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.integration_type == IntegrationType.SLACK,
        )
    )
    integration = result.scalar_one_or_none()
    
    if integration:
        integration.is_active = False
        integration.access_token = None
        integration.refresh_token = None
        await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse('<div class="text-green-500">Slack disconnected</div>')
    
    return RedirectResponse(
        url="/profile/integrations",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/slack/sync-channels")
async def slack_sync_channels(
    request: Request,
    db: DBSession,
    user: CurrentUser,
    workspace_id: int = Form(...),
):
    """
    Auto-sync Slack channels to a Forge workspace.
    Creates Forge channels with 'SLACK:' prefix for each Slack channel
    and creates bridges between them.
    """
    # Get user's active Slack integration
    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.integration_type == IntegrationType.SLACK,
            ExternalIntegration.is_active == True,
        )
    )
    integration = result.scalar_one_or_none()
    
    if not integration or not integration.access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active Slack integration found",
        )
    
    # Verify user has access to the workspace
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    
    # Fetch Slack channels
    slack_channels = await slack_service.list_channels(
        integration.access_token,
        types="public_channel,private_channel"  # Only sync actual channels, not DMs
    )
    
    if not slack_channels:
        if request.headers.get("HX-Request"):
            return HTMLResponse('<div class="text-yellow-500">No Slack channels found to sync</div>')
        return RedirectResponse(
            url=f"/integrations/bridges?warning=no_channels",
            status_code=status.HTTP_302_FOUND,
        )
    
    synced_count = 0
    skipped_count = 0
    
    for slack_channel in slack_channels:
        channel_id = slack_channel.get("id")
        channel_name = slack_channel.get("name", "unnamed")
        is_private = slack_channel.get("is_private", False)
        
        # Skip if already bridged
        result = await db.execute(
            select(BridgedChannel).where(
                BridgedChannel.external_channel_id == channel_id,
                BridgedChannel.integration_id == integration.id,
            )
        )
        if result.scalar_one_or_none():
            skipped_count += 1
            continue
        
        # Create Forge channel with SLACK: prefix
        forge_channel_name = f"SLACK:{channel_name}"
        
        # Check if channel with this name already exists in workspace
        result = await db.execute(
            select(Channel).where(
                Channel.workspace_id == workspace_id,
                Channel.name == forge_channel_name,
            )
        )
        existing_channel = result.scalar_one_or_none()
        
        if existing_channel:
            # Use existing channel
            forge_channel = existing_channel
        else:
            # Create new channel
            forge_channel = Channel(
                workspace_id=workspace_id,
                name=forge_channel_name,
                description=f"Synced from Slack: #{channel_name}",
                is_private=is_private,
                is_dm=False,
                is_archived=False,
            )
            db.add(forge_channel)
            await db.flush()
            
            # Add user as member of the channel
            membership = ChannelMembership(
                channel_id=forge_channel.id,
                user_id=user.id,
            )
            db.add(membership)
        
        # Create bridge
        bridge = BridgedChannel(
            channel_id=forge_channel.id,
            integration_id=integration.id,
            platform=BridgePlatform.slack,
            external_channel_id=channel_id,
            external_channel_name=channel_name,
            sync_incoming=True,
            sync_outgoing=True,
            reply_prefix="From Forge:",
        )
        db.add(bridge)
        synced_count += 1
    
    await db.commit()
    
    message = f"Synced {synced_count} Slack channels"
    if skipped_count > 0:
        message += f" (skipped {skipped_count} already bridged)"
    
    if request.headers.get("HX-Request"):
        return HTMLResponse(f'<div class="text-green-500">{message}</div>')
    
    return RedirectResponse(
        url=f"/integrations/bridges?synced={synced_count}",
        status_code=status.HTTP_302_FOUND,
    )


# ==================== Discord OAuth ====================

@router.get("/discord/connect")
async def discord_connect(
    request: Request,
    user: CurrentUser,
):
    """Start Discord OAuth flow."""
    if not settings.discord_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discord integration is not configured",
        )
    
    # Build redirect URI
    proto = request.headers.get("X-Forwarded-Proto", "https" if not settings.debug else "http")
    host = request.headers.get("X-Forwarded-Host", request.url.netloc)
    redirect_uri = f"{proto}://{host}/integrations/discord/callback"
    
    # Generate state token
    state = secrets.token_urlsafe(32)
    
    # Get authorization URL
    auth_url = discord_service.get_authorization_url(state, redirect_uri)
    
    response = RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="discord_oauth_state",
        value=state,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        max_age=600,
    )
    response.set_cookie(
        key="discord_redirect_uri",
        value=redirect_uri,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        max_age=600,
    )
    return response


@router.get("/discord/callback")
async def discord_callback(
    request: Request,
    db: DBSession,
    user: CurrentUser,
    code: str = Query(...),
    state: str = Query(...),
):
    """Handle Discord OAuth callback."""
    # Verify state
    stored_state = request.cookies.get("discord_oauth_state")
    redirect_uri = request.cookies.get("discord_redirect_uri")
    
    if not stored_state or state != stored_state:
        return RedirectResponse(
            url="/profile/integrations?error=Invalid+state",
            status_code=status.HTTP_302_FOUND,
        )
    
    # Exchange code for tokens
    token_data = await discord_service.exchange_code_for_token(code, redirect_uri)
    
    if not token_data:
        return RedirectResponse(
            url="/profile/integrations?error=Failed+to+connect+Discord",
            status_code=status.HTTP_302_FOUND,
        )
    
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")
    
    # Get Discord user info
    discord_user = await discord_service.get_current_user(access_token)
    
    if not discord_user:
        return RedirectResponse(
            url="/profile/integrations?error=Failed+to+get+Discord+user",
            status_code=status.HTTP_302_FOUND,
        )
    
    # Get or create integration record
    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.integration_type == IntegrationType.DISCORD,
        )
    )
    integration = result.scalar_one_or_none()
    
    if not integration:
        integration = ExternalIntegration(
            user_id=user.id,
            integration_type=IntegrationType.DISCORD,
            notification_preferences={
                "dm": True,
                "mentions": True,
                "guilds": [],
            },
        )
        db.add(integration)
    
    # Update tokens and info
    integration.update_tokens(access_token, refresh_token, expires_in)
    integration.external_user_id = discord_user.get("id")
    integration.external_username = f"{discord_user.get('username')}#{discord_user.get('discriminator', '0')}"
    integration.is_active = True
    
    await db.commit()
    
    # Clear OAuth cookies
    response = RedirectResponse(
        url="/profile/integrations?success=discord",
        status_code=status.HTTP_302_FOUND,
    )
    response.delete_cookie("discord_oauth_state")
    response.delete_cookie("discord_redirect_uri")
    return response


@router.post("/discord/disconnect")
async def discord_disconnect(
    request: Request,
    db: DBSession,
    user: CurrentUser,
):
    """Disconnect Discord integration."""
    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.integration_type == IntegrationType.DISCORD,
        )
    )
    integration = result.scalar_one_or_none()
    
    if integration:
        # Revoke token
        if integration.access_token:
            await discord_service.revoke_token(integration.access_token)
        
        integration.is_active = False
        integration.access_token = None
        integration.refresh_token = None
        await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse('<div class="text-green-500">Discord disconnected</div>')
    
    return RedirectResponse(
        url="/profile/integrations",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/discord/sync-channels")
async def discord_sync_channels(
    request: Request,
    db: DBSession,
    user: CurrentUser,
    workspace_id: int = Form(...),
):
    """
    Auto-sync Discord channels to a Forge workspace.
    Creates Forge channels with 'DISCORD:' prefix for each Discord channel
    and creates bridges between them.
    """
    # Get user's active Discord integration
    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.integration_type == IntegrationType.DISCORD,
            ExternalIntegration.is_active == True,
        )
    )
    integration = result.scalar_one_or_none()
    
    if not integration or not integration.access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active Discord integration found",
        )
    
    # Verify user has access to the workspace
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    
    # Fetch Discord guilds the user has access to
    guilds = await discord_service.get_user_guilds(integration.access_token)
    
    if not guilds:
        if request.headers.get("HX-Request"):
            return HTMLResponse('<div class="text-yellow-500">No Discord servers found</div>')
        return RedirectResponse(
            url=f"/integrations/bridges?warning=no_guilds",
            status_code=status.HTTP_302_FOUND,
        )
    
    synced_count = 0
    skipped_count = 0
    
    # For now, sync text channels from guilds
    for guild in guilds:
        guild_id = guild.get("id")
        guild_name = guild.get("name", "unnamed")
        
        # Try to get guild channels (requires bot with appropriate permissions)
        channels = await discord_service.get_guild_channels(guild_id)
        
        if not channels:
            continue
        
        for discord_channel in channels:
            # Only sync text channels
            if discord_channel.get("type") != 0:  # 0 = text channel
                continue
            
            channel_id = discord_channel.get("id")
            channel_name = discord_channel.get("name", "unnamed")
            
            # Skip if already bridged
            result = await db.execute(
                select(BridgedChannel).where(
                    BridgedChannel.external_channel_id == channel_id,
                    BridgedChannel.integration_id == integration.id,
                )
            )
            if result.scalar_one_or_none():
                skipped_count += 1
                continue
            
            # Create Forge channel with DISCORD: prefix
            forge_channel_name = f"DISCORD:{guild_name}:{channel_name}"[:80]  # Limit name length
            
            # Check if channel with this name already exists in workspace
            result = await db.execute(
                select(Channel).where(
                    Channel.workspace_id == workspace_id,
                    Channel.name == forge_channel_name,
                )
            )
            existing_channel = result.scalar_one_or_none()
            
            if existing_channel:
                forge_channel = existing_channel
            else:
                forge_channel = Channel(
                    workspace_id=workspace_id,
                    name=forge_channel_name,
                    description=f"Synced from Discord: {guild_name} #{channel_name}",
                    is_private=False,
                    is_dm=False,
                    is_archived=False,
                )
                db.add(forge_channel)
                await db.flush()
                
                membership = ChannelMembership(
                    channel_id=forge_channel.id,
                    user_id=user.id,
                )
                db.add(membership)
            
            # Create bridge
            bridge = BridgedChannel(
                channel_id=forge_channel.id,
                integration_id=integration.id,
                platform=BridgePlatform.discord,
                external_channel_id=channel_id,
                external_channel_name=f"{guild_name}#{channel_name}",
                sync_incoming=True,
                sync_outgoing=True,
                reply_prefix="From Forge:",
            )
            db.add(bridge)
            synced_count += 1
    
    await db.commit()
    
    message = f"Synced {synced_count} Discord channels"
    if skipped_count > 0:
        message += f" (skipped {skipped_count} already bridged)"
    
    if request.headers.get("HX-Request"):
        return HTMLResponse(f'<div class="text-green-500">{message}</div>')
    
    return RedirectResponse(
        url=f"/integrations/bridges?synced={synced_count}",
        status_code=status.HTTP_302_FOUND,
    )


# ==================== Notification Settings ====================

@router.post("/{integration_type}/settings")
async def update_integration_settings(
    request: Request,
    db: DBSession,
    user: CurrentUser,
    integration_type: str,
    dm: Annotated[str | None, Form()] = None,
    mentions: Annotated[str | None, Form()] = None,
    channels: Annotated[str | None, Form()] = None,
):
    """Update notification preferences for an integration."""
    try:
        int_type = IntegrationType(integration_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid integration type")
    
    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.integration_type == int_type,
        )
    )
    integration = result.scalar_one_or_none()
    
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    
    # Update preferences
    prefs = integration.notification_preferences or {}
    prefs["dm"] = dm == "true"
    prefs["mentions"] = mentions == "true"
    
    # Parse channel list (comma-separated)
    if channels:
        prefs["channels"] = [c.strip() for c in channels.split(",") if c.strip()]
    else:
        prefs["channels"] = []
    
    integration.notification_preferences = prefs
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse('<div class="text-green-500 text-sm">Settings saved!</div>')
    
    return RedirectResponse(
        url="/profile/integrations",
        status_code=status.HTTP_302_FOUND,
    )


# ==================== Notifications Feed ====================

@router.get("/notifications", response_class=HTMLResponse)
async def notifications_feed(
    request: Request,
    db: DBSession,
    user: CurrentUser,
    page: int = 1,
    per_page: int = 50,
):
    """View aggregated notifications from Slack/Discord."""
    # Get user's integrations
    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.is_active == True,
        )
    )
    integrations = result.scalars().all()
    
    # Get notifications
    offset = (page - 1) * per_page
    result = await db.execute(
        select(NotificationLog)
        .where(NotificationLog.user_id == user.id)
        .order_by(NotificationLog.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    notifications = result.scalars().all()
    
    # Count unread
    result = await db.execute(
        select(NotificationLog)
        .where(
            NotificationLog.user_id == user.id,
            NotificationLog.is_read == False,
        )
    )
    unread_count = len(result.scalars().all())
    
    return templates.TemplateResponse(
        "integrations/notifications.html",
        {
            "request": request,
            "user": user,
            "integrations": integrations,
            "notifications": notifications,
            "unread_count": unread_count,
            "page": page,
            "per_page": per_page,
            "has_more": len(notifications) == per_page,
        },
    )


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    request: Request,
    db: DBSession,
    user: CurrentUser,
    notification_id: int,
):
    """Mark a notification as read."""
    result = await db.execute(
        select(NotificationLog).where(
            NotificationLog.id == notification_id,
            NotificationLog.user_id == user.id,
        )
    )
    notification = result.scalar_one_or_none()
    
    if notification:
        notification.is_read = True
        await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    
    return JSONResponse({"status": "ok"})


@router.post("/notifications/read-all")
async def mark_all_notifications_read(
    request: Request,
    db: DBSession,
    user: CurrentUser,
):
    """Mark all notifications as read."""
    from sqlalchemy import update
    
    await db.execute(
        update(NotificationLog)
        .where(
            NotificationLog.user_id == user.id,
            NotificationLog.is_read == False,
        )
        .values(is_read=True)
    )
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse('<span class="text-green-500">All marked as read</span>')
    
    return RedirectResponse(
        url="/integrations/notifications",
        status_code=status.HTTP_302_FOUND,
    )


# ==================== Webhook Endpoints ====================

@router.post("/slack/webhook")
async def slack_webhook(
    request: Request,
    db: DBSession,
):
    """
    Handle Slack Events API webhooks.
    
    Note: This requires a Slack App with Event Subscriptions enabled.
    The app needs to subscribe to message events.
    """
    body = await request.body()
    
    # Verify signature
    signature = request.headers.get("X-Slack-Signature", "")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    
    if not slack_service.verify_webhook_signature(signature, timestamp, body):
        logger.warning("Invalid Slack webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    data = await request.json()
    
    # Handle URL verification challenge
    if data.get("type") == "url_verification":
        return JSONResponse({"challenge": data.get("challenge")})
    
    # Handle events
    if data.get("type") == "event_callback":
        event_data = slack_service.parse_event(data)

        if event_data:
            slack_channel_id = event_data.get("channel_id")
            slack_team_id = data.get("team_id")
            slack_user_id = event_data.get("user_id")
            message_text = event_data.get("text", "")
            message_ts = event_data.get("ts")

            # Find all active integrations for this Slack workspace.
            result = await db.execute(
                select(ExternalIntegration).where(
                    ExternalIntegration.integration_type == IntegrationType.SLACK,
                    ExternalIntegration.external_team_id == slack_team_id,
                    ExternalIntegration.is_active == True,
                )
            )
            integrations = result.scalars().all()

            # Find the Forge channel bridged to this Slack channel (shared across users).
            bridge_result = await db.execute(
                select(BridgedChannel).where(
                    BridgedChannel.external_channel_id == slack_channel_id,
                    BridgedChannel.is_active == True,
                    BridgedChannel.platform == BridgePlatform.slack,
                )
            )
            bridge = bridge_result.scalar_one_or_none()

            # Store the message in the Forge Message table so it appears in
            # conversation lists and drives unread counts. Only store once per
            # external_message_id to avoid duplicates on webhook retries.
            stored_message = None
            if bridge and message_ts:
                existing = await db.execute(
                    select(Message).where(
                        Message.channel_id == bridge.channel_id,
                        Message.external_message_id == message_ts,
                        Message.external_source == "slack",
                    )
                )
                if existing.scalar_one_or_none() is None:
                    sender_name = "Unknown"
                    if integrations:
                        sender_info = await slack_service.get_user_info(
                            integrations[0].access_token, slack_user_id
                        )
                        if sender_info:
                            sender_name = sender_info.get("real_name") or sender_info.get("name", "Unknown")

                    stored_message = Message(
                        channel_id=bridge.channel_id,
                        user_id=None,  # no Forge user — external sender
                        body=message_text,
                        external_source="slack",
                        external_author_name=sender_name,
                        external_message_id=message_ts,
                        created_at=datetime.fromtimestamp(float(message_ts), tz=timezone.utc),
                    )
                    db.add(stored_message)
                    await db.flush()  # get stored_message.id before using it below

            for integration in integrations:
                prefs = integration.notification_preferences or {}
                source = event_data.get("source")

                # Determine whether this user should be notified.
                # For bridged channels, always notify members — the bridge itself
                # is the signal that they want to see these messages.
                should_notify = False
                if source == "slack_dm" and prefs.get("dm", True):
                    should_notify = True
                elif source == "slack_channel":
                    if bridge:
                        # Always notify for channels the user has bridged.
                        should_notify = True
                    elif prefs.get("mentions"):
                        if f"<@{integration.external_user_id}>" in message_text:
                            source = "slack_mention"
                            should_notify = True
                    # Also check explicit watched list (Slack channel IDs, not Forge IDs).
                    watched = prefs.get("channels", [])
                    if slack_channel_id in watched:
                        should_notify = True

                if should_notify:
                    channel_info = await slack_service.get_channel_info(
                        integration.access_token, slack_channel_id
                    )
                    channel_name = channel_info.get("name") if channel_info else None

                    sender_name = "Unknown"
                    sender_info = await slack_service.get_user_info(
                        integration.access_token, slack_user_id
                    )
                    if sender_info:
                        sender_name = sender_info.get("real_name") or sender_info.get("name", "Unknown")

                    external_url = slack_service.build_message_url(
                        slack_team_id, slack_channel_id, message_ts
                    )

                    notification = NotificationLog.create_from_slack(
                        user_id=integration.user_id,
                        integration_id=integration.id,
                        source=NotificationSource(source),
                        sender_name=sender_name,
                        message_body=message_text,
                        channel_name=channel_name,
                        external_url=external_url,
                        sender_external_id=slack_user_id,
                        channel_external_id=slack_channel_id,
                        external_message_id=message_ts,
                    )
                    db.add(notification)

            await db.commit()

    return JSONResponse({"status": "ok"})


@router.post("/discord/webhook")
async def discord_webhook(
    request: Request,
    db: DBSession,
):
    """
    Handle Discord webhook events.
    
    Note: Discord doesn't have user-level webhooks like Slack.
    This endpoint is for bot-based notifications if a bot is configured.
    For OAuth-only integrations, we use polling instead.
    """
    # Discord uses different verification
    # For Interactions endpoint, verify signature
    signature = request.headers.get("X-Signature-Ed25519")
    timestamp = request.headers.get("X-Signature-Timestamp")
    
    if not signature or not timestamp:
        raise HTTPException(status_code=401, detail="Missing signature")
    
    data = await request.json()
    
    # Handle ping
    if data.get("type") == 1:
        return JSONResponse({"type": 1})
    
    # Process other events...
    # This would require more complex bot setup
    
    return JSONResponse({"status": "ok"})


# ==================== Channel Bridging ====================

@router.get("/slack/channels")
async def list_slack_channels(
    request: Request,
    user: CurrentUser,
    db: DBSession,
):
    """List available Slack channels to bridge."""
    # Get user's Slack integration
    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.integration_type == IntegrationType.slack,
            ExternalIntegration.is_active == True,
        )
    )
    integration = result.scalar_one_or_none()
    
    if not integration or not integration.access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack integration not connected",
        )
    
    channels = await slack_service.list_channels(integration.access_token)
    return JSONResponse({"channels": channels})


@router.get("/discord/channels")
async def list_discord_channels(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    guild_id: str = Query(..., description="Discord server/guild ID"),
):
    """List available Discord channels to bridge."""
    # Get user's Discord integration
    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.integration_type == IntegrationType.discord,
            ExternalIntegration.is_active == True,
        )
    )
    integration = result.scalar_one_or_none()
    
    if not integration or not integration.access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discord integration not connected",
        )
    
    channels = await discord_service.get_guild_channels(
        integration.access_token, 
        guild_id
    )
    return JSONResponse({"channels": channels})


@router.get("/bridges", response_class=HTMLResponse)
async def list_bridges(
    request: Request,
    user: CurrentUser,
    db: DBSession,
):
    """List all bridged channels for the user."""
    result = await db.execute(
        select(BridgedChannel)
        .options(
            selectinload(BridgedChannel.channel),
            selectinload(BridgedChannel.integration),
        )
        .join(BridgedChannel.channel)
        .where(Channel.owner_id == user.id)
    )
    bridges = result.scalars().all()
    
    # Get user's workspaces for sync dropdown
    result = await db.execute(
        select(Workspace)
        .join(Membership, Workspace.id == Membership.workspace_id)
        .where(Membership.user_id == user.id)
    )
    workspaces = result.scalars().all()
    
    return templates.TemplateResponse(
        "integrations/bridges.html",
        {
            "request": request,
            "user": user,
            "bridges": bridges,
            "workspaces": workspaces,
        },
    )


@router.post("/bridges")
async def create_bridge(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    channel_id: int = Form(...),
    integration_id: int = Form(...),
    external_channel_id: str = Form(...),
    external_channel_name: str = Form(...),
    sync_incoming: bool = Form(True),
    sync_outgoing: bool = Form(True),
    reply_prefix: str = Form("From Buildly Communicator:"),
):
    """Create a bridge between a Forge channel and an external channel."""
    # Verify user owns the channel
    result = await db.execute(
        select(Channel).where(
            Channel.id == channel_id,
            Channel.owner_id == user.id,
        )
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    # Verify user owns the integration
    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.id == integration_id,
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.is_active == True,
        )
    )
    integration = result.scalar_one_or_none()
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    
    # Determine platform from integration type
    platform = BridgePlatform.slack if integration.integration_type == IntegrationType.slack else BridgePlatform.discord
    
    # Check if bridge already exists
    result = await db.execute(
        select(BridgedChannel).where(
            BridgedChannel.channel_id == channel_id,
            BridgedChannel.external_channel_id == external_channel_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This channel is already bridged",
        )
    
    # Create the bridge
    bridge = BridgedChannel(
        channel_id=channel_id,
        integration_id=integration_id,
        platform=platform,
        external_channel_id=external_channel_id,
        external_channel_name=external_channel_name,
        sync_incoming=sync_incoming,
        sync_outgoing=sync_outgoing,
        reply_prefix=reply_prefix,
    )
    db.add(bridge)
    await db.commit()
    await db.refresh(bridge)
    
    return RedirectResponse(
        url=f"/integrations/bridges?created={bridge.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.delete("/bridges/{bridge_id}")
async def delete_bridge(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    bridge_id: int,
):
    """Delete a channel bridge."""
    result = await db.execute(
        select(BridgedChannel)
        .options(selectinload(BridgedChannel.channel))
        .where(BridgedChannel.id == bridge_id)
    )
    bridge = result.scalar_one_or_none()
    
    if not bridge:
        raise HTTPException(status_code=404, detail="Bridge not found")
    
    if bridge.channel.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    await db.delete(bridge)
    await db.commit()
    
    return JSONResponse({"status": "deleted"})


@router.post("/bridges/{bridge_id}/import")
async def import_bridge_history(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    bridge_id: int,
    limit: int = Query(10, ge=1, le=100),
):
    """Import message history from an external channel."""
    result = await db.execute(
        select(BridgedChannel)
        .options(
            selectinload(BridgedChannel.channel),
            selectinload(BridgedChannel.integration),
        )
        .where(BridgedChannel.id == bridge_id)
    )
    bridge = result.scalar_one_or_none()
    
    if not bridge:
        raise HTTPException(status_code=404, detail="Bridge not found")
    
    if bridge.channel.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    integration = bridge.integration
    if not integration.access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Integration not properly connected",
        )
    
    imported_count = 0
    
    if bridge.platform == BridgePlatform.slack:
        # Fetch Slack history
        messages = await slack_service.get_channel_history(
            integration.access_token,
            bridge.external_channel_id,
            limit=limit,
        )
        
        # Get user info for messages
        user_ids = list(set(m.get("user") for m in messages if m.get("user")))
        users_info = await slack_service.get_users_by_ids(
            integration.access_token, 
            user_ids
        )
        
        for msg in messages:
            # Check if already imported
            result = await db.execute(
                select(Message).where(
                    Message.external_message_id == msg.get("ts"),
                    Message.channel_id == bridge.channel_id,
                )
            )
            if result.scalar_one_or_none():
                continue  # Skip already imported
            
            user_info = users_info.get(msg.get("user"), {})
            
            # Create message in Forge
            new_msg = Message(
                channel_id=bridge.channel_id,
                body=msg.get("text", ""),
                external_source="slack",
                external_message_id=msg.get("ts"),
                external_channel_id=bridge.external_channel_id,
                external_thread_ts=msg.get("thread_ts"),
                external_author_name=user_info.get("name", "Unknown"),
                external_author_avatar=user_info.get("avatar"),
                created_at=datetime.fromtimestamp(
                    float(msg.get("ts", 0)), 
                    tz=timezone.utc
                ),
            )
            db.add(new_msg)
            imported_count += 1
    
    elif bridge.platform == BridgePlatform.discord:
        # Fetch Discord history
        messages = await discord_service.get_channel_messages(
            integration.access_token,
            bridge.external_channel_id,
            limit=limit,
        )
        
        for msg in messages:
            # Check if already imported
            result = await db.execute(
                select(Message).where(
                    Message.external_message_id == msg.get("id"),
                    Message.channel_id == bridge.channel_id,
                )
            )
            if result.scalar_one_or_none():
                continue  # Skip already imported
            
            author = msg.get("author", {})
            avatar_url = None
            if author.get("avatar"):
                avatar_url = f"https://cdn.discordapp.com/avatars/{author.get('id')}/{author.get('avatar')}.png"
            
            # Create message in Forge
            new_msg = Message(
                channel_id=bridge.channel_id,
                body=msg.get("content", ""),
                external_source="discord",
                external_message_id=msg.get("id"),
                external_channel_id=bridge.external_channel_id,
                external_author_name=author.get("username", "Unknown"),
                external_author_avatar=avatar_url,
                created_at=datetime.fromisoformat(
                    msg.get("timestamp", "").replace("Z", "+00:00")
                ) if msg.get("timestamp") else datetime.now(timezone.utc),
            )
            db.add(new_msg)
            imported_count += 1
    
    await db.commit()
    
    # Update last sync time
    bridge.last_sync_at = datetime.now(timezone.utc)
    await db.commit()
    
    return JSONResponse({
        "status": "imported",
        "count": imported_count,
    })


@router.post("/bridges/{bridge_id}/reply")
async def reply_to_external(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    bridge_id: int,
    content: str = Form(...),
    thread_ts: str = Form(None),  # For Slack threads
    reply_to_id: str = Form(None),  # For Discord replies
):
    """Send a reply to an external channel through a bridge."""
    result = await db.execute(
        select(BridgedChannel)
        .options(
            selectinload(BridgedChannel.channel),
            selectinload(BridgedChannel.integration),
        )
        .where(BridgedChannel.id == bridge_id)
    )
    bridge = result.scalar_one_or_none()
    
    if not bridge:
        raise HTTPException(status_code=404, detail="Bridge not found")
    
    if bridge.channel.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if not bridge.sync_outgoing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Outgoing sync is disabled for this bridge",
        )
    
    integration = bridge.integration
    if not integration.access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Integration not properly connected",
        )
    
    # Format the message with bridge prefix
    formatted_content = bridge.format_outgoing_message(content)
    
    external_message_id = None
    
    if bridge.platform == BridgePlatform.slack:
        result = await slack_service.post_message(
            integration.access_token,
            bridge.external_channel_id,
            formatted_content,
            thread_ts=thread_ts,
        )
        external_message_id = result.get("ts")
    
    elif bridge.platform == BridgePlatform.discord:
        result = await discord_service.post_message(
            integration.access_token,
            bridge.external_channel_id,
            formatted_content,
        )
        external_message_id = result.get("id")
    
    # Create a record in Forge messages too
    new_msg = Message(
        channel_id=bridge.channel_id,
        user_id=user.id,
        body=content,
        external_source=bridge.platform.value,
        external_message_id=external_message_id,
        external_channel_id=bridge.external_channel_id,
        external_thread_ts=thread_ts,
    )
    db.add(new_msg)
    await db.commit()
    await db.refresh(new_msg)
    
    return JSONResponse({
        "status": "sent",
        "message_id": new_msg.id,
        "external_message_id": external_message_id,
    })
