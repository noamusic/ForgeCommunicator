"""
User profile router.
"""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.deps import CurrentUser, DBSession
from app.models.user import User, UserStatus
from app.models.user_session import UserSession
from app.models.membership import Membership
from app.models.workspace import Workspace
from app.models.external_integration import ExternalIntegration, IntegrationType
from app.settings import settings
from app.templates_config import templates

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_class=HTMLResponse)
async def my_profile(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    pending: Annotated[str | None, Query()] = None,
):
    """View and edit own profile."""
    # Get user's workspaces
    result = await db.execute(
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
        .order_by(Workspace.name)
    )
    workspaces = result.scalars().all()
    
    # Show pending approval message if user is not approved
    show_pending_message = pending == "true" or not user.is_approved
    
    return templates.TemplateResponse(
        "profile/edit.html",
        {
            "request": request,
            "user": user,
            "profile_user": user,
            "workspaces": workspaces,
            "is_own_profile": True,
            "statuses": UserStatus,
            "google_enabled": settings.google_oauth_enabled,
            "pending_approval": show_pending_message,
        },
    )


@router.post("", response_class=HTMLResponse)
async def update_profile(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    display_name: Annotated[str, Form()],
    bio: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    phone: Annotated[str | None, Form()] = None,
    timezone: Annotated[str | None, Form()] = None,
    status: Annotated[str | None, Form()] = None,
    status_message: Annotated[str | None, Form()] = None,
):
    """Update own profile."""
    if not display_name or not display_name.strip():
        if request.headers.get("HX-Request"):
            return HTMLResponse('<div class="text-red-500">Display name is required</div>', status_code=400)
        raise HTTPException(status_code=400, detail="Display name is required")
    
    user.display_name = display_name.strip()[:100]
    user.bio = bio.strip() if bio else None
    user.title = title.strip()[:100] if title else None
    user.phone = phone.strip()[:30] if phone else None
    user.timezone = timezone.strip()[:50] if timezone else "UTC"
    
    if status and status in [s.value for s in UserStatus]:
        user.status = UserStatus(status)
    
    user.status_message = status_message.strip()[:100] if status_message else None
    
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            '<div class="text-green-600 dark:text-green-400">Profile updated successfully!</div>',
            headers={"HX-Trigger": "profileUpdated"}
        )
    
    return RedirectResponse(url="/profile", status_code=status.HTTP_302_FOUND)


@router.get("/integrations", response_class=HTMLResponse)
async def integrations_page(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    success: str | None = None,
    error: str | None = None,
):
    """View and manage external integrations (Slack, Discord)."""
    # Get user's integrations
    result = await db.execute(
        select(ExternalIntegration).where(ExternalIntegration.user_id == user.id)
    )
    integrations = {i.integration_type: i for i in result.scalars().all()}
    
    return templates.TemplateResponse(
        "profile/integrations.html",
        {
            "request": request,
            "user": user,
            "slack_integration": integrations.get(IntegrationType.SLACK),
            "discord_integration": integrations.get(IntegrationType.DISCORD),
            "slack_enabled": settings.slack_enabled,
            "discord_enabled": settings.discord_enabled,
            "success": success,
            "error": error,
        },
    )


@router.post("/avatar", response_class=HTMLResponse)
async def update_avatar_url(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    avatar_url: Annotated[str | None, Form()] = None,
):
    """Update avatar URL (use external image URL)."""
    if avatar_url:
        # Basic URL validation
        avatar_url = avatar_url.strip()
        if not avatar_url.startswith(('http://', 'https://')):
            if request.headers.get("HX-Request"):
                return HTMLResponse('<div class="text-red-500">Please enter a valid URL starting with http:// or https://</div>', status_code=400)
            raise HTTPException(status_code=400, detail="Invalid URL")
        
        user.avatar_url = avatar_url[:500]
    else:
        user.avatar_url = None
    
    await db.commit()
    
    if request.headers.get("HX-Request"):
        if user.avatar_url:
            return HTMLResponse(f'''
                <div class="text-green-600 dark:text-green-400 mb-2">Avatar updated!</div>
                <img src="{user.avatar_url}" alt="Avatar" class="w-32 h-32 rounded-full object-cover">
            ''')
        else:
            return HTMLResponse(f'''
                <div class="text-green-600 dark:text-green-400 mb-2">Avatar removed!</div>
                <div class="w-32 h-32 rounded-full bg-indigo-500 flex items-center justify-center">
                    <span class="text-white text-4xl font-medium">{user.display_name[0]}</span>
                </div>
            ''')
    
    return RedirectResponse(url="/profile", status_code=status.HTTP_302_FOUND)


@router.get("/user/{user_id}", response_class=HTMLResponse)
async def view_user_profile(
    request: Request,
    user_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """View another user's profile."""
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)
    )
    profile_user = result.scalar_one_or_none()
    
    if not profile_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    
    # Get shared workspaces
    result = await db.execute(
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(
            Membership.user_id == profile_user.id,
            Workspace.id.in_(
                select(Membership.workspace_id).where(Membership.user_id == user.id)
            )
        )
        .order_by(Workspace.name)
    )
    shared_workspaces = result.scalars().all()
    
    return templates.TemplateResponse(
        "profile/view.html",
        {
            "request": request,
            "user": user,
            "profile_user": profile_user,
            "shared_workspaces": shared_workspaces,
            "is_own_profile": user.id == profile_user.id,
            "google_enabled": settings.google_oauth_enabled,
        },
    )


@router.post("/user/{user_id}/meeting", response_class=HTMLResponse)
async def request_meeting(
    request: Request,
    user_id: int,
    user: CurrentUser,
    db: DBSession,
    title: Annotated[str, Form()],
    date: Annotated[str, Form()],
    start_time: Annotated[str, Form()],
    duration: Annotated[int, Form()],  # Duration in minutes
    description: Annotated[str | None, Form()] = None,
):
    """Request a meeting with another user via Google Calendar."""
    from datetime import datetime, timedelta
    from app.services.auth_providers import GoogleOAuthProvider
    from app.services.google_calendar import refresh_google_token_if_needed
    
    # Check if requester has Google linked
    if not user.has_google_linked:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<div class="text-red-400">You need to connect your Google account to request meetings. '
                '<a href="/profile" class="underline">Go to your profile</a> to connect.</div>',
                status_code=400,
            )
        raise HTTPException(status_code=400, detail="Google account not linked")
    
    # Get target user
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)
    )
    target_user = result.scalar_one_or_none()
    
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Cannot request meeting with yourself
    if user.id == target_user.id:
        if request.headers.get("HX-Request"):
            return HTMLResponse('<div class="text-red-400">You cannot request a meeting with yourself.</div>', status_code=400)
        raise HTTPException(status_code=400, detail="Cannot request meeting with yourself")
    
    # Validate and parse date/time
    try:
        # Parse date (YYYY-MM-DD) and time (HH:MM)
        meeting_date = datetime.strptime(date, "%Y-%m-%d")
        time_parts = start_time.split(":")
        start_dt = meeting_date.replace(hour=int(time_parts[0]), minute=int(time_parts[1]))
        end_dt = start_dt + timedelta(minutes=duration)
        
        # Format as RFC3339 with timezone
        # Get user's timezone or default to UTC
        user_tz = user.timezone or "UTC"
        start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:00")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:00")
        
    except (ValueError, IndexError) as e:
        if request.headers.get("HX-Request"):
            return HTMLResponse('<div class="text-red-400">Invalid date or time format.</div>', status_code=400)
        raise HTTPException(status_code=400, detail="Invalid date or time format")
    
    # Get valid access token
    access_token = await refresh_google_token_if_needed(user, db)
    if not access_token:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<div class="text-red-400">Your Google connection has expired. '
                '<a href="/auth/google/link" class="underline">Re-authorize</a> your account.</div>',
                status_code=400,
            )
        raise HTTPException(status_code=400, detail="Google token expired")
    
    # Create calendar event
    try:
        google_provider = GoogleOAuthProvider(include_calendar=True)
        
        # Build attendees list (target user)
        attendees = [target_user.email]
        
        # Build description
        full_description = f"Meeting requested by {user.display_name} via Forge Communicator"
        if description:
            full_description += f"\n\n{description}"
        
        event = await google_provider.create_meeting_event(
            access_token=access_token,
            summary=title,
            start_time=start_iso,
            end_time=end_iso,
            attendees=attendees,
            description=full_description,
        )
        
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'''
                <div class="bg-green-500/20 border border-green-500/30 rounded-lg p-4 text-center">
                    <svg class="w-8 h-8 mx-auto text-green-400 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
                    </svg>
                    <p class="text-green-400 font-medium">Meeting request sent!</p>
                    <p class="text-gray-400 text-sm mt-1">A calendar invite has been sent to {target_user.display_name}.</p>
                </div>
            ''', headers={"HX-Trigger": "meeting-requested"})
        
        return RedirectResponse(
            url=f"/profile/user/{user_id}?success=Meeting+request+sent",
            status_code=status.HTTP_302_FOUND,
        )
        
    except Exception as e:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                f'<div class="text-red-400">Failed to create meeting: Could not send calendar invite.</div>',
                status_code=500,
            )
        raise HTTPException(status_code=500, detail=f"Failed to create meeting")


# =============================================================================
# Session Management - View and revoke active sessions
# =============================================================================

@router.get("/sessions", response_class=HTMLResponse)
async def list_sessions(
    request: Request,
    user: CurrentUser,
    db: DBSession,
):
    """View all active sessions for current user."""
    # Get all sessions for user
    result = await db.execute(
        select(UserSession)
        .where(UserSession.user_id == user.id)
        .order_by(UserSession.last_used_at.desc())
    )
    sessions = result.scalars().all()
    
    # Identify current session
    current_token = request.cookies.get("session_token")
    
    return templates.TemplateResponse(
        "profile/sessions.html",
        {
            "request": request,
            "user": user,
            "sessions": sessions,
            "current_token": current_token,
        },
    )


@router.post("/sessions/{session_id}/revoke")
async def revoke_session(
    request: Request,
    session_id: int,
    user: CurrentUser,
    db: DBSession,
):
    """Revoke a specific session."""
    # Find the session
    result = await db.execute(
        select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == user.id,  # Only allow revoking own sessions
        )
    )
    session = result.scalar_one_or_none()
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Check if trying to revoke current session
    current_token = request.cookies.get("session_token")
    is_current = session.session_token == current_token
    
    # Delete the session
    await db.delete(session)
    await db.commit()
    
    if request.headers.get("HX-Request"):
        if is_current:
            # If revoking current session, redirect to login
            response = HTMLResponse("")
            response.headers["HX-Redirect"] = "/auth/login"
            response.delete_cookie("session_token")
            return response
        else:
            # Just remove the row from the table
            return HTMLResponse("")
    
    if is_current:
        response = RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
        response.delete_cookie("session_token")
        return response
    
    return RedirectResponse(url="/profile/sessions", status_code=status.HTTP_302_FOUND)


@router.post("/sessions/revoke-all-others")
async def revoke_all_other_sessions(
    request: Request,
    user: CurrentUser,
    db: DBSession,
):
    """Revoke all sessions except the current one."""
    current_token = request.cookies.get("session_token")
    
    # Get all other sessions
    result = await db.execute(
        select(UserSession).where(
            UserSession.user_id == user.id,
            UserSession.session_token != current_token,
        )
    )
    other_sessions = result.scalars().all()
    
    # Delete them
    for session in other_sessions:
        await db.delete(session)
    
    await db.commit()
    
    if request.headers.get("HX-Request"):
        # Return success message and trigger table refresh
        return HTMLResponse(
            '''<div class="bg-green-500/20 border border-green-500/30 rounded-lg p-3 text-green-400 text-sm mb-4">
                All other sessions have been signed out.
            </div>''',
            headers={"HX-Trigger": "sessions-updated"},
        )
    
    return RedirectResponse(url="/profile/sessions?success=1", status_code=status.HTTP_302_FOUND)
