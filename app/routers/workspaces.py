"""
Workspace management router.
"""

import re
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps import CurrentUser, DBSession, WorkspaceCreator
from app.models.artifact import Artifact
from app.models.channel import Channel
from app.models.membership import Membership, MembershipRole
from app.models.message import Message
from app.models.product import Product
from app.models.team_invite import TeamInvite, InviteStatus
from app.models.workspace import Workspace
from app.settings import settings
from app.templates_config import templates

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def slugify(name: str) -> str:
    """Convert name to URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[-\s]+', '-', slug)
    return slug[:50]


@router.get("", response_class=HTMLResponse)
async def list_workspaces(
    request: Request,
    user: CurrentUser,
    db: DBSession,
):
    """List user's workspaces."""
    from app.settings import settings
    from sqlalchemy import func as sqlfunc
    from app.models.channel import Channel
    from app.models.membership import ChannelMembership
    from app.models.message import Message
    
    # Get workspaces user is a member of
    result = await db.execute(
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
        .options(selectinload(Workspace.memberships))
    )
    workspaces = result.scalars().all()
    
    # Calculate unread counts per workspace
    workspace_unread_counts = {}
    for ws in workspaces:
        # Get all channels in this workspace the user can access
        result = await db.execute(
            select(Channel.id)
            .where(
                Channel.workspace_id == ws.id,
                Channel.is_archived == False,
            )
        )
        channel_ids = [row[0] for row in result.fetchall()]
        
        if not channel_ids:
            workspace_unread_counts[ws.id] = 0
            continue
        
        # Get user's last read positions for these channels
        result = await db.execute(
            select(ChannelMembership)
            .where(
                ChannelMembership.user_id == user.id,
                ChannelMembership.channel_id.in_(channel_ids),
            )
        )
        user_memberships = {cm.channel_id: cm.last_read_message_id for cm in result.scalars().all()}
        
        # Count unread messages across all channels in this workspace
        total_unread = 0
        for ch_id in channel_ids:
            last_read_id = user_memberships.get(ch_id)
            if last_read_id is not None:
                count_result = await db.execute(
                    select(sqlfunc.count(Message.id))
                    .where(
                        Message.channel_id == ch_id,
                        Message.deleted_at == None,
                        Message.parent_id == None,  # Only top-level messages, not thread replies
                        Message.id > last_read_id,
                        Message.user_id != user.id,
                    )
                )
            else:
                # No membership - count all messages except own (and thread replies)
                count_result = await db.execute(
                    select(sqlfunc.count(Message.id))
                    .where(
                        Message.channel_id == ch_id,
                        Message.deleted_at == None,
                        Message.parent_id == None,  # Only top-level messages, not thread replies
                        Message.user_id != user.id,
                    )
                )
            total_unread += count_result.scalar() or 0
        
        workspace_unread_counts[ws.id] = total_unread
    
    # Check if user is admin (either flagged or in admin emails)
    is_admin = user.is_platform_admin or settings.is_admin_email(user.email)
    
    # Check if workspace creation is allowed for this user
    from app.models.site_config import SiteConfig, ConfigKeys
    result = await db.execute(
        select(SiteConfig).where(SiteConfig.key == ConfigKeys.REQUIRE_WORKSPACE_CREATE_APPROVAL)
    )
    config = result.scalar_one_or_none()
    require_ws_approval = config and config.value == "true"
    can_create_workspaces = is_admin or user.can_create_workspaces or not require_ws_approval
    
    return templates.TemplateResponse(
        "workspaces/list.html",
        {
            "request": request,
            "user": user,
            "workspaces": workspaces,
            "workspace_unread_counts": workspace_unread_counts,
            "is_admin": is_admin,
            "can_create_workspaces": can_create_workspaces,
        },
    )


@router.get("/unread-count", response_class=JSONResponse)
async def get_total_unread_count(
    user: CurrentUser,
    db: DBSession,
):
    """Get total unread message count across all workspaces for badge sync."""
    from sqlalchemy import func as sqlfunc
    from app.models.channel import Channel
    from app.models.membership import ChannelMembership
    from app.models.message import Message
    
    # Get all workspaces user is a member of
    result = await db.execute(
        select(Workspace.id)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
    )
    workspace_ids = [row[0] for row in result.fetchall()]
    
    if not workspace_ids:
        return JSONResponse({"unread_count": 0})
    
    # Get all channels across all workspaces
    result = await db.execute(
        select(Channel.id)
        .where(
            Channel.workspace_id.in_(workspace_ids),
            Channel.is_archived == False,
        )
    )
    channel_ids = [row[0] for row in result.fetchall()]
    
    if not channel_ids:
        return JSONResponse({"unread_count": 0})
    
    # Get user's last read positions
    result = await db.execute(
        select(ChannelMembership)
        .where(
            ChannelMembership.user_id == user.id,
            ChannelMembership.channel_id.in_(channel_ids),
        )
    )
    user_memberships = {cm.channel_id: cm.last_read_message_id for cm in result.scalars().all()}
    
    # Count unread across all channels
    total_unread = 0
    for ch_id in channel_ids:
        last_read_id = user_memberships.get(ch_id)
        if last_read_id is not None:
            count_result = await db.execute(
                select(sqlfunc.count(Message.id))
                .where(
                    Message.channel_id == ch_id,
                    Message.deleted_at == None,
                    Message.parent_id == None,
                    Message.id > last_read_id,
                    Message.user_id != user.id,
                )
            )
        else:
            count_result = await db.execute(
                select(sqlfunc.count(Message.id))
                .where(
                    Message.channel_id == ch_id,
                    Message.deleted_at == None,
                    Message.parent_id == None,
                    Message.user_id != user.id,
                )
            )
        total_unread += count_result.scalar() or 0
    
    return JSONResponse({"unread_count": total_unread})


@router.post("/{workspace_id}/mark-all-read", response_class=HTMLResponse)
async def mark_workspace_all_read(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Mark all messages in all channels of a workspace as read."""
    # Verify membership
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    
    # Get all channels in workspace
    result = await db.execute(
        select(Channel.id).where(
            Channel.workspace_id == workspace_id,
            Channel.is_archived == False,
        )
    )
    channel_ids = [row[0] for row in result.fetchall()]
    
    if not channel_ids:
        if request.headers.get("HX-Request"):
            return HTMLResponse('<span class="text-green-500 text-sm">All caught up!</span>')
        return RedirectResponse(f"/workspaces/{workspace_id}", status_code=303)
    
    # For each channel, get latest message and update membership
    for ch_id in channel_ids:
        # Get latest message ID
        result = await db.execute(
            select(Message.id)
            .where(Message.channel_id == ch_id, Message.deleted_at == None)
            .order_by(Message.id.desc())
            .limit(1)
        )
        latest_id = result.scalar_one_or_none()
        
        if latest_id is None:
            continue
        
        # Update or create membership
        result = await db.execute(
            select(ChannelMembership).where(
                ChannelMembership.channel_id == ch_id,
                ChannelMembership.user_id == user.id,
            )
        )
        membership = result.scalar_one_or_none()
        
        if membership:
            membership.last_read_message_id = latest_id
        else:
            db.add(ChannelMembership(
                channel_id=ch_id,
                user_id=user.id,
                last_read_message_id=latest_id,
            ))
    
    await db.commit()
    
    if request.headers.get("HX-Request"):
        # Return updated badge count (0) and trigger refresh
        return HTMLResponse(
            '<span class="text-green-500 text-sm">All caught up!</span>',
            headers={"HX-Trigger": "refreshUnreadCounts"}
        )
    
    return RedirectResponse(f"/workspaces/{workspace_id}", status_code=303)


@router.get("/new", response_class=HTMLResponse)
async def new_workspace_form(
    request: Request,
    user: CurrentUser,
):
    """Render new workspace form."""
    return templates.TemplateResponse(
        "workspaces/new.html",
        {
            "request": request,
            "user": user,
        },
    )


@router.post("/new")
async def create_workspace(
    request: Request,
    user: WorkspaceCreator,  # Requires workspace creation permission
    db: DBSession,
    name: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
):
    """Create a new workspace."""
    # Generate slug
    slug = slugify(name)
    
    # Check if slug exists
    result = await db.execute(
        select(Workspace).where(Workspace.slug == slug)
    )
    if result.scalar_one_or_none():
        # Add suffix to make unique
        import time
        slug = f"{slug}-{int(time.time()) % 10000}"
    
    # Create workspace
    workspace = Workspace(
        name=name.strip(),
        slug=slug,
        description=description.strip() if description else None,
    )
    workspace.generate_invite_code()
    db.add(workspace)
    await db.flush()
    
    # Add creator as owner
    membership = Membership(
        workspace_id=workspace.id,
        user_id=user.id,
        role=MembershipRole.OWNER,
    )
    db.add(membership)
    
    # Create default #general channel
    general = Channel(
        workspace_id=workspace.id,
        name="general",
        description="General discussion",
        is_default=True,
    )
    db.add(general)
    
    await db.commit()
    
    if request.headers.get("HX-Request"):
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = f"/workspaces/{workspace.id}"
        return response
    
    return RedirectResponse(
        url=f"/workspaces/{workspace.id}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/join", response_class=HTMLResponse)
async def join_workspace_form(
    request: Request,
    user: CurrentUser,
    code: str | None = None,
):
    """Render join workspace form."""
    return templates.TemplateResponse(
        "workspaces/join.html",
        {
            "request": request,
            "user": user,
            "code": code,
        },
    )


@router.post("/join")
async def join_workspace(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    invite_code: Annotated[str, Form()],
):
    """Join a workspace via invite code."""
    # Find workspace by invite code
    result = await db.execute(
        select(Workspace).where(Workspace.invite_code == invite_code.upper().strip())
    )
    workspace = result.scalar_one_or_none()
    
    if not workspace or not workspace.is_invite_valid(invite_code.upper().strip()):
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<div class="text-red-500">Invalid or expired invite code</div>',
                status_code=400,
            )
        return RedirectResponse(
            url="/workspaces/join?error=Invalid+invite+code",
            status_code=status.HTTP_302_FOUND,
        )
    
    # Check if already a member
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace.id,
            Membership.user_id == user.id,
        )
    )
    if result.scalar_one_or_none():
        # Already a member, just redirect
        if request.headers.get("HX-Request"):
            response = HTMLResponse("")
            response.headers["HX-Redirect"] = f"/workspaces/{workspace.id}"
            return response
        return RedirectResponse(
            url=f"/workspaces/{workspace.id}",
            status_code=status.HTTP_302_FOUND,
        )
    
    # Add membership
    membership = Membership(
        workspace_id=workspace.id,
        user_id=user.id,
        role=MembershipRole.MEMBER,
    )
    db.add(membership)
    await db.commit()
    
    if request.headers.get("HX-Request"):
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = f"/workspaces/{workspace.id}"
        return response
    
    return RedirectResponse(
        url=f"/workspaces/{workspace.id}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/{workspace_id}", response_class=HTMLResponse)
async def workspace_home(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Workspace home page - redirects to first channel."""
    # Check membership
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")
    
    # Get workspace
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    # Get default channel
    result = await db.execute(
        select(Channel)
        .where(Channel.workspace_id == workspace_id, Channel.is_default == True)
        .limit(1)
    )
    default_channel = result.scalar_one_or_none()
    
    if not default_channel:
        # Get any channel
        result = await db.execute(
            select(Channel)
            .where(Channel.workspace_id == workspace_id, Channel.is_archived == False)
            .limit(1)
        )
        default_channel = result.scalar_one_or_none()
    
    if default_channel:
        return RedirectResponse(
            url=f"/workspaces/{workspace_id}/channels/{default_channel.id}",
            status_code=status.HTTP_302_FOUND,
        )
    
    # No channels, render workspace settings
    return templates.TemplateResponse(
        "workspaces/settings.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "membership": membership,
        },
    )


@router.get("/{workspace_id}/settings", response_class=HTMLResponse)
async def workspace_settings(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Workspace settings page."""
    # Check membership
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")
    
    # Get workspace
    result = await db.execute(
        select(Workspace)
        .where(Workspace.id == workspace_id)
        .options(selectinload(Workspace.memberships).selectinload(Membership.user))
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    # Get pending invites
    result = await db.execute(
        select(TeamInvite)
        .where(
            TeamInvite.workspace_id == workspace_id,
            TeamInvite.status == InviteStatus.PENDING,
        )
        .order_by(TeamInvite.created_at.desc())
    )
    pending_invites = result.scalars().all()
    
    # Get channels for management
    result = await db.execute(
        select(Channel)
        .where(
            Channel.workspace_id == workspace_id,
            Channel.is_archived == False,
        )
        .options(selectinload(Channel.product))
        .order_by(Channel.name)
    )
    channels = result.scalars().all()
    
    # Get products for management
    result = await db.execute(
        select(Product)
        .where(Product.workspace_id == workspace_id)
        .order_by(Product.name)
    )
    products = result.scalars().all()
    
    # Get artifacts (docs) for management
    result = await db.execute(
        select(Artifact)
        .where(Artifact.workspace_id == workspace_id)
        .options(selectinload(Artifact.product))
        .order_by(Artifact.title)
    )
    artifacts = result.scalars().all()
    
    return templates.TemplateResponse(
        "workspaces/settings.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "membership": membership,
            "pending_invites": pending_invites,
            "channels": channels,
            "products": products,
            "artifacts": artifacts,
            "email_configured": settings.email_configured,
        },
    )


@router.post("/{workspace_id}/invite/regenerate")
async def regenerate_invite(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Regenerate invite code (admin only)."""
    # Check admin
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership or membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    # Get workspace and regenerate code
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    workspace.generate_invite_code()
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            f'<span id="invite-code" class="font-mono bg-gray-100 px-2 py-1 rounded">{workspace.invite_code}</span>',
        )
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/settings",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{workspace_id}/invites")
async def create_team_invite(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
    email: Annotated[str, Form()],
    name: Annotated[str | None, Form()] = None,
    send_email: Annotated[str | None, Form()] = None,
    cc_self: Annotated[str | None, Form()] = None,
    cc_emails: Annotated[str | None, Form()] = None,
):
    """Create a new team invite (admin only).
    
    Args:
        email: Email address to invite
        name: Optional name of the invitee
        send_email: Whether to send the invite email (checkbox - "true" if checked)
        cc_self: Whether to CC the inviter on the email (checkbox - "true" if checked)
        cc_emails: Comma-separated list of additional emails to CC
    """
    # Convert checkbox values (checkboxes send "true" when checked, nothing when unchecked)
    should_send_email = send_email == "true"
    should_cc_self = cc_self == "true"
    # Check admin
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership or membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    email = email.lower().strip()
    
    # Check if already a member
    from app.models.user import User
    result = await db.execute(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.workspace_id == workspace_id,
            User.email == email,
        )
    )
    if result.scalar_one_or_none():
        if request.headers.get("HX-Request"):
            return HTMLResponse('<div class="text-red-500 text-sm">This user is already a member</div>', status_code=400)
        raise HTTPException(status_code=400, detail="User is already a member")
    
    # Check for existing pending invite
    result = await db.execute(
        select(TeamInvite).where(
            TeamInvite.workspace_id == workspace_id,
            TeamInvite.email == email,
            TeamInvite.status == InviteStatus.PENDING,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        if request.headers.get("HX-Request"):
            return HTMLResponse('<div class="text-yellow-600 text-sm">Invite already pending for this email</div>', status_code=400)
        raise HTTPException(status_code=400, detail="Invite already pending")
    
    # Get workspace name for email
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one()
    
    # Create invite
    invite = TeamInvite.create(
        workspace_id=workspace_id,
        email=email,
        name=name.strip() if name else None,
        invited_by_id=user.id,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    
    # Send email if requested
    email_sent = False
    email_status_msg = ""
    if should_send_email:
        from app.services.email import send_invite_email, email_service
        
        # Build CC list
        cc_list: list[str] = []
        if should_cc_self:
            cc_list.append(user.email)
        if cc_emails:
            additional_ccs = [e.strip().lower() for e in cc_emails.split(",") if e.strip() and "@" in e]
            cc_list.extend(additional_ccs)
        
        email_sent = await send_invite_email(
            to_email=email,
            invite_token=invite.token,
            workspace_name=workspace.name,
            inviter_name=user.display_name,
            cc_emails=cc_list if cc_list else None,
        )
        
        if email_sent:
            email_status_msg = '<span class="text-green-500 text-xs ml-2">✓ Email sent</span>'
        elif email_service.is_configured:
            email_status_msg = '<span class="text-yellow-500 text-xs ml-2">⚠ Email failed</span>'
        else:
            email_status_msg = '<span class="text-gray-400 text-xs ml-2">(Email not configured)</span>'
    
    if request.headers.get("HX-Request"):
        # Return the new invite row HTML
        return HTMLResponse(f'''
            <li class="py-3 flex items-center justify-between" id="invite-{invite.id}">
                <div class="flex items-center">
                    <div class="w-8 h-8 rounded-full bg-yellow-100 flex items-center justify-center">
                        <svg class="w-4 h-4 text-yellow-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
                        </svg>
                    </div>
                    <div class="ml-3">
                        <p class="text-sm font-medium text-gray-900">{invite.name or invite.email}{email_status_msg}</p>
                        <p class="text-sm text-gray-500">{invite.email}</p>
                    </div>
                </div>
                <div class="flex items-center space-x-3">
                    <span class="text-xs text-gray-400">Expires {invite.expires_at.strftime('%b %d')}</span>
                    <button onclick="copyInviteLink('{invite.token}')" class="text-xs text-indigo-600 hover:text-indigo-500">Copy Link</button>
                    <button hx-post="/workspaces/{workspace_id}/invites/{invite.id}/resend" hx-swap="none" class="text-xs text-blue-600 hover:text-blue-500">Resend</button>
                    <button hx-delete="/workspaces/{workspace_id}/invites/{invite.id}" hx-target="#invite-{invite.id}" hx-swap="outerHTML" class="text-xs text-red-600 hover:text-red-500">Cancel</button>
                </div>
            </li>
        ''')
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/settings",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{workspace_id}/invites/{invite_id}/resend")
async def resend_team_invite(
    request: Request,
    workspace_id: int,
    invite_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Resend an invite email (admin only)."""
    # Check admin
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership or membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    # Get invite
    result = await db.execute(
        select(TeamInvite).where(
            TeamInvite.id == invite_id,
            TeamInvite.workspace_id == workspace_id,
            TeamInvite.status == InviteStatus.PENDING,
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    
    # Get workspace
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one()
    
    # Resend email
    from app.services.email import send_invite_email
    
    email_sent = await send_invite_email(
        to_email=invite.email,
        invite_token=invite.token,
        workspace_name=workspace.name,
        inviter_name=user.display_name,
    )
    
    if request.headers.get("HX-Request"):
        if email_sent:
            return HTMLResponse('<span class="text-green-500 text-xs">✓ Email resent</span>')
        else:
            return HTMLResponse('<span class="text-red-500 text-xs">Failed to send</span>')
    
    return {"success": email_sent}


@router.delete("/{workspace_id}/invites/{invite_id}")
async def cancel_team_invite(
    request: Request,
    workspace_id: int,
    invite_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Cancel a pending invite (admin only)."""
    # Check admin
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership or membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    # Get and cancel invite
    result = await db.execute(
        select(TeamInvite).where(
            TeamInvite.id == invite_id,
            TeamInvite.workspace_id == workspace_id,
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    
    invite.status = InviteStatus.CANCELLED
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse("")  # Remove the row
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/settings",
        status_code=status.HTTP_302_FOUND,
    )


@router.delete("/{workspace_id}/products/{product_id}")
async def delete_product(
    request: Request,
    workspace_id: int,
    product_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Delete a product and its associated channels/artifacts (admin only)."""
    # Check admin
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership or membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    # Get and delete product
    result = await db.execute(
        select(Product).where(
            Product.id == product_id,
            Product.workspace_id == workspace_id,
        )
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    
    # Delete associated artifacts first
    await db.execute(
        Artifact.__table__.delete().where(Artifact.product_id == product_id)
    )
    
    # Delete associated channels
    await db.execute(
        Channel.__table__.delete().where(Channel.product_id == product_id)
    )
    
    # Delete product
    await db.delete(product)
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse("")  # Remove the row
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/settings",
        status_code=status.HTTP_302_FOUND,
    )


@router.delete("/{workspace_id}/artifacts/{artifact_id}")
async def delete_artifact(
    request: Request,
    workspace_id: int,
    artifact_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Delete an artifact/document (admin only)."""
    # Check admin
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership or membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    # Get and delete artifact
    result = await db.execute(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.workspace_id == workspace_id,
        )
    )
    artifact = result.scalar_one_or_none()
    if not artifact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    
    await db.delete(artifact)
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse("")  # Remove the row
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/settings",
        status_code=status.HTTP_302_FOUND,
    )


@router.delete("/{workspace_id}/products")
async def delete_all_products(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Delete all products in workspace (admin only) - bulk cleanup."""
    # Check admin
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership or membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    # Delete all artifacts in workspace
    await db.execute(
        Artifact.__table__.delete().where(Artifact.workspace_id == workspace_id)
    )
    
    # Delete all product-linked channels
    await db.execute(
        Channel.__table__.delete().where(
            Channel.workspace_id == workspace_id,
            Channel.product_id.isnot(None)
        )
    )
    
    # Delete all products
    await db.execute(
        Product.__table__.delete().where(Product.workspace_id == workspace_id)
    )
    
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse('<div class="text-green-400">All products deleted successfully. Refresh the page to see changes.</div>')
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/settings",
        status_code=status.HTTP_302_FOUND,
    )


@router.put("/{workspace_id}")
async def update_workspace(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
    name: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
):
    """Update workspace name and description (owner only)."""
    # Check owner
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership or membership.role != MembershipRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required")
    
    # Get workspace
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    # Update workspace
    workspace.name = name.strip()
    workspace.slug = slugify(name)
    workspace.description = description.strip() if description else None
    
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse(f'''
            <div class="text-green-600 text-sm mb-2">Workspace updated successfully!</div>
            <script>
                setTimeout(() => {{
                    document.querySelector('.text-green-600')?.remove();
                }}, 3000);
            </script>
        ''')
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/settings",
        status_code=status.HTTP_302_FOUND,
    )


@router.put("/{workspace_id}/sync-settings")
async def update_sync_settings(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
    labs_default_product_uuid: Annotated[str | None, Form()] = None,
    github_repo: Annotated[str | None, Form()] = None,
    github_token: Annotated[str | None, Form()] = None,
):
    """Update workspace sync settings (Labs default product, GitHub config)."""
    # Check admin access
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership or membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    # Get workspace
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    # Update settings
    updated_fields = []
    
    if labs_default_product_uuid is not None:
        workspace.labs_default_product_uuid = labs_default_product_uuid if labs_default_product_uuid else None
        updated_fields.append("Labs default product")
    
    if github_repo is not None:
        workspace.github_repo = github_repo.strip() if github_repo else None
        updated_fields.append("GitHub repository")
    
    if github_token is not None and github_token.strip():
        # Only update token if a new one was provided (not empty placeholder)
        workspace.github_token = github_token.strip()
        updated_fields.append("GitHub token")
    
    await db.commit()
    
    if request.headers.get("HX-Request"):
        field_list = ", ".join(updated_fields) if updated_fields else "Settings"
        return HTMLResponse(f'''
            <div class="text-green-400 text-sm">✓ {field_list} saved</div>
            <script>
                setTimeout(() => {{
                    document.querySelector('.text-green-400')?.remove();
                }}, 3000);
            </script>
        ''')
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/settings",
        status_code=status.HTTP_302_FOUND,
    )


@router.put("/{workspace_id}/notification-settings")
async def update_notification_settings(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
    notify_all_messages: Annotated[str | None, Form()] = None,
):
    """Update the current user's notification preferences for a workspace."""
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a workspace member")

    membership.notify_all_messages = notify_all_messages == "true"
    await db.commit()

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            '<div class="text-green-400 text-sm">✓ Notification preferences saved</div>'
            "<script>setTimeout(() => { document.querySelector('.text-green-400')?.remove(); }, 3000);</script>"
        )

    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/settings",
        status_code=status.HTTP_302_FOUND,
    )


@router.delete("/{workspace_id}")
async def delete_workspace(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Delete workspace (owner only). This deletes EVERYTHING in the workspace."""
    # Check owner
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership or membership.role != MembershipRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required")
    
    # Get workspace
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    # Delete all related data (in order due to foreign keys)
    # Artifacts
    await db.execute(
        Artifact.__table__.delete().where(Artifact.workspace_id == workspace_id)
    )
    
    # Team invites
    await db.execute(
        TeamInvite.__table__.delete().where(TeamInvite.workspace_id == workspace_id)
    )
    
    # Channels (this will cascade delete messages)
    await db.execute(
        Channel.__table__.delete().where(Channel.workspace_id == workspace_id)
    )
    
    # Products
    await db.execute(
        Product.__table__.delete().where(Product.workspace_id == workspace_id)
    )
    
    # Memberships
    await db.execute(
        Membership.__table__.delete().where(Membership.workspace_id == workspace_id)
    )
    
    # Finally delete the workspace
    await db.delete(workspace)
    await db.commit()
    
    if request.headers.get("HX-Request"):
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = "/workspaces"
        return response
    
    return RedirectResponse(
        url="/workspaces",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{workspace_id}/labs/api-token")
async def configure_labs_api_token(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
    api_token: Annotated[str, Form()],
    org_uuid: Annotated[str | None, Form()] = None,
):
    """Configure Labs API token for workspace (admin only)."""
    # Check admin
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership or membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    # Get workspace
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    # Validate token by making a test API call
    try:
        from app.services.labs_sync import LabsSyncService
        service = LabsSyncService(api_key=api_token.strip())
        me = await service.get_me()
        labs_email = me.get("data", {}).get("email", "unknown")
    except Exception as e:
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'<div class="text-red-600 text-sm">❌ Invalid API token: {str(e)}</div>')
        raise HTTPException(status_code=400, detail=f"Invalid API token: {str(e)}")
    
    # Update workspace
    workspace.labs_api_token = api_token.strip()
    workspace.labs_connected_by_id = user.id
    if org_uuid:
        workspace.buildly_org_uuid = org_uuid.strip()
    
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse(f'''
            <div class="text-green-600 text-sm">✓ Labs API token configured successfully! Connected as {labs_email}</div>
            <script>setTimeout(() => location.reload(), 1500);</script>
        ''')
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/settings",
        status_code=status.HTTP_302_FOUND,
    )


@router.delete("/{workspace_id}/labs/disconnect")
async def disconnect_labs(
    request: Request,
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Disconnect Labs integration from workspace (admin only)."""
    # Check admin
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership or membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    # Get workspace
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    # Clear Labs integration
    workspace.labs_api_token = None
    workspace.labs_access_token = None
    workspace.labs_refresh_token = None
    workspace.labs_token_expires_at = None
    workspace.labs_connected_by_id = None
    workspace.buildly_org_uuid = None
    
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse('''
            <div class="text-green-600 text-sm">✓ Labs integration disconnected</div>
            <script>setTimeout(() => location.reload(), 1500);</script>
        ''')
    
    return RedirectResponse(
        url=f"/workspaces/{workspace_id}/settings",
        status_code=status.HTTP_302_FOUND,
    )
