"""
FastAPI dependencies for authentication, database, and more.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models.membership import Membership, MembershipRole
from app.models.user import User
from app.models.user_session import UserSession
from app.models.workspace import Workspace
from app.settings import settings

# Type alias for database dependency
DBSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user_optional(
    request: Request,
    db: DBSession,
    session_token: str | None = Cookie(default=None),
) -> User | None:
    """Get current user from session cookie (returns None if not authenticated).
    
    Uses the user_sessions table for multi-device session support.
    Each device/browser has its own session, so logging in on one device
    doesn't invalidate sessions on other devices.
    
    Session behavior:
    - Sessions are automatically refreshed (sliding window) when < 50% time remains
    - PWA sessions have longer expiration (30 days vs 7 days for browser)
    - For OAuth users, recently expired sessions (1 hour grace) can be restored
    """
    if not session_token:
        return None
    
    # Look up session in user_sessions table
    result = await db.execute(
        select(UserSession)
        .where(UserSession.session_token == session_token)
        .options(selectinload(UserSession.user))
    )
    session = result.scalar_one_or_none()
    
    if not session or not session.user:
        # Fallback: Check old single-session token on user table (for migration)
        # This can be removed after all sessions have been migrated
        result = await db.execute(
            select(User).where(User.session_token == session_token)
        )
        user = result.scalar_one_or_none()
        if user and user.is_session_valid():
            # Migrate this session to the new table
            is_pwa = _detect_pwa_mode(request)
            new_session = UserSession.create_session(
                user_id=user.id,
                request=request,
                is_pwa=is_pwa,
            )
            # Use the same token for seamless migration
            new_session.session_token = session_token
            db.add(new_session)
            # Clear old token from user table
            user.session_token = None
            user.session_expires_at = None
            user.update_last_seen()
            await db.commit()
            request.state.session_refreshed = True
            return user
        return None
    
    user = session.user
    
    # Check if session is valid (not expired)
    session_valid = session.is_valid()
    
    # For OAuth users, try to restore recently expired sessions (1 hour grace period)
    if not session_valid:
        grace_period = timedelta(hours=1)
        time_since_expiry = datetime.now(timezone.utc) - session.expires_at
        
        # Check if within grace period and user has OAuth refresh capability
        if time_since_expiry <= grace_period:
            can_auto_refresh = (
                (user.auth_provider.value == "google" and user.google_refresh_token) or
                (user.auth_provider.value == "buildly" and user.labs_refresh_token)
            )
            if can_auto_refresh:
                # Restore session for OAuth users with valid refresh tokens
                expire_hours = settings.session_expire_hours_pwa if session.is_pwa else settings.session_expire_hours
                session.expires_at = datetime.now(timezone.utc) + timedelta(hours=expire_hours)
                session.last_used_at = datetime.now(timezone.utc)
                user.update_last_seen()
                await db.commit()
                request.state.session_refreshed = True
                session_valid = True
    
    if session_valid:
        # Refresh session using sliding window
        session.refresh()
        user.update_last_seen()
        
        # Commit if session was extended
        if session in db.dirty:
            await db.commit()
            request.state.session_refreshed = True
        
        return user
    
    return None


def _detect_pwa_mode(request: Request) -> bool:
    """Detect if request is from a PWA (installed app mode)."""
    return (
        request.headers.get('X-PWA-Mode') == 'standalone' or
        'standalone' in request.headers.get('Sec-Fetch-Dest', '')
    )


async def get_current_user(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user_optional)],
) -> User:
    """Get current user from session cookie (raises 401 if not authenticated).
    
    For browser requests, this will trigger a redirect to /auth/login via the
    401 exception handler in main.py.
    """
    if not user:
        # Check if this is an HTMX request
        if request.headers.get("HX-Request"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"HX-Redirect": "/auth/login"},
            )
        # For regular browser requests, just raise 401 - the exception handler will redirect
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return user


# Type alias for authenticated user dependency
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentUserOptional = Annotated[User | None, Depends(get_current_user_optional)]


async def require_approved_user(
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> User:
    """Require user to have an approved account.
    
    Unapproved users can only access their profile page.
    """
    from app.models.site_config import SiteConfig, ConfigKeys
    
    # Check if approval is required
    result = await db.execute(
        select(SiteConfig).where(SiteConfig.key == ConfigKeys.REQUIRE_ACCOUNT_APPROVAL)
    )
    config = result.scalar_one_or_none()
    approval_required = config and config.value == "true"
    
    if approval_required and not user.is_approved:
        # Allow access to profile and auth routes
        path = request.url.path
        allowed_paths = ["/profile", "/auth/logout", "/static/"]
        if any(path.startswith(p) for p in allowed_paths):
            return user
        
        # For HTMX requests
        if request.headers.get("HX-Request"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account pending approval",
                headers={"HX-Redirect": "/profile?pending=true"},
            )
        # Redirect to profile with message
        from fastapi.responses import RedirectResponse
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account pending approval. Please wait for an administrator to approve your account.",
        )
    
    return user


async def require_workspace_create_permission(
    user: CurrentUser,
    db: DBSession,
) -> User:
    """Require user to have permission to create workspaces.
    
    Checks if workspace creation approval is required and if user has permission.
    """
    from app.models.site_config import SiteConfig, ConfigKeys
    
    # Check if workspace creation approval is required
    result = await db.execute(
        select(SiteConfig).where(SiteConfig.key == ConfigKeys.REQUIRE_WORKSPACE_CREATE_APPROVAL)
    )
    config = result.scalar_one_or_none()
    approval_required = config and config.value == "true"
    
    if approval_required and not user.can_create_workspaces:
        # Platform admins can always create workspaces
        if user.is_platform_admin or settings.is_admin_email(user.email):
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to create workspaces. Please contact an administrator.",
        )
    
    return user


# Type aliases for approved user checks
ApprovedUser = Annotated[User, Depends(require_approved_user)]
WorkspaceCreator = Annotated[User, Depends(require_workspace_create_permission)]


async def get_workspace_membership(
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
) -> Membership:
    """Verify user is a member of the workspace and return membership."""
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this workspace",
        )
    return membership


async def require_workspace_admin(
    workspace_id: int,
    user: CurrentUser,
    db: DBSession,
) -> Membership:
    """Require user to be admin or owner of the workspace."""
    membership = await get_workspace_membership(workspace_id, user, db)
    
    if membership.role not in (MembershipRole.OWNER, MembershipRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return membership


async def get_workspace_by_id(
    workspace_id: int,
    db: DBSession,
) -> Workspace:
    """Get workspace by ID."""
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )
    return workspace


def get_request_id(request: Request) -> str:
    """Get or generate request ID for logging."""
    return request.headers.get("X-Request-ID", request.state.request_id)
