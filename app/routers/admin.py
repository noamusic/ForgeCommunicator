"""
Platform admin routes for user management and configuration.
"""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from app.brand import clear_brand_cache, get_brand_with_overrides
from app.deps import CurrentUser, DBSession
from app.models.user import User
from app.models.workspace import Workspace
from app.models.membership import Membership
from app.models.site_config import SiteConfig, ConfigKeys, THEME_PRESETS
from app.settings import settings
from app.templates_config import templates


router = APIRouter(prefix="/admin", tags=["admin"])


async def require_platform_admin(user: User) -> None:
    """Require user to be a platform admin."""
    if not user.is_platform_admin and not settings.is_admin_email(user.email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin access required",
        )


async def get_config_dict(db) -> dict[str, str]:
    """Get all config values as a dict."""
    result = await db.execute(select(SiteConfig))
    configs = result.scalars().all()
    return {c.key: c.value for c in configs}


async def set_config(db, key: str, value: str | None, user_id: int) -> None:
    """Set a config value."""
    result = await db.execute(
        select(SiteConfig).where(SiteConfig.key == key)
    )
    config = result.scalar_one_or_none()
    
    if config:
        if value:
            config.value = value
            config.updated_by = user_id
        else:
            # Delete if value is empty
            await db.delete(config)
    elif value:
        config = SiteConfig(key=key, value=value, updated_by=user_id)
        db.add(config)


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    user: CurrentUser,
    db: DBSession,
):
    """Admin dashboard overview."""
    await require_platform_admin(user)
    
    # Get counts
    user_count = await db.scalar(select(func.count()).select_from(User))
    workspace_count = await db.scalar(select(func.count()).select_from(Workspace))
    
    # Get pending approval count
    pending_approval_count = await db.scalar(
        select(func.count()).select_from(User).where(User.is_approved == False)
    )
    
    # Recent users
    result = await db.execute(
        select(User).order_by(User.created_at.desc()).limit(10)
    )
    recent_users = result.scalars().all()
    
    # Integration status for debugging
    integration_status = {
        "slack_enabled": settings.slack_enabled,
        "slack_client_id_set": bool(settings.slack_client_id),
        "slack_client_secret_set": bool(settings.slack_client_secret),
        "discord_enabled": settings.discord_enabled,
        "discord_client_id_set": bool(settings.discord_client_id),
        "discord_client_secret_set": bool(settings.discord_client_secret),
        "google_oauth_enabled": settings.google_oauth_enabled,
        "buildly_oauth_enabled": settings.buildly_oauth_enabled,
        "push_enabled": settings.push_enabled,
        "email_configured": settings.email_configured,
    }
    
    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "user_count": user_count,
            "workspace_count": workspace_count,
            "pending_approval_count": pending_approval_count or 0,
            "recent_users": recent_users,
            "registration_mode": settings.registration_mode,
            "integration_status": integration_status,
        },
    )


@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    page: int = Query(default=1, ge=1),
    search: str | None = None,
):
    """List all users with pagination and search."""
    await require_platform_admin(user)
    
    per_page = 25
    offset = (page - 1) * per_page
    
    # Build query
    query = select(User)
    count_query = select(func.count()).select_from(User)
    
    if search:
        search_filter = User.email.ilike(f"%{search}%") | User.display_name.ilike(f"%{search}%")
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)
    
    # Get total count
    total = await db.scalar(count_query) or 0
    total_pages = (total + per_page - 1) // per_page
    
    # Get users
    result = await db.execute(
        query.order_by(User.created_at.desc()).offset(offset).limit(per_page)
    )
    users = result.scalars().all()
    
    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "search": search or "",
        },
    )


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    user_id: int,
):
    """View/edit a specific user."""
    await require_platform_admin(user)
    
    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get user's workspaces
    result = await db.execute(
        select(Membership).where(Membership.user_id == user_id)
    )
    memberships = result.scalars().all()
    
    return templates.TemplateResponse(
        "admin/user_detail.html",
        {
            "request": request,
            "user": user,
            "target_user": target_user,
            "memberships": memberships,
        },
    )


@router.post("/users/{user_id}/toggle-active")
async def toggle_user_active(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    user_id: int,
):
    """Toggle user active status."""
    await require_platform_admin(user)
    
    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Can't deactivate yourself
    if target_user.id == user.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    
    target_user.is_active = not target_user.is_active
    await db.commit()
    
    if request.headers.get("HX-Request"):
        status_text = "Active" if target_user.is_active else "Inactive"
        status_class = "bg-green-100 text-green-800" if target_user.is_active else "bg-red-100 text-red-800"
        return HTMLResponse(
            f'<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium {status_class}">{status_text}</span>'
        )
    
    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/toggle-admin")
async def toggle_user_admin(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    user_id: int,
):
    """Toggle user platform admin status."""
    await require_platform_admin(user)
    
    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Can't remove your own admin
    if target_user.id == user.id:
        raise HTTPException(status_code=400, detail="Cannot modify your own admin status")
    
    target_user.is_platform_admin = not target_user.is_platform_admin
    await db.commit()
    
    if request.headers.get("HX-Request"):
        if target_user.is_platform_admin:
            return HTMLResponse(
                '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-800">Admin</span>'
            )
        return HTMLResponse('<span class="text-gray-400 text-sm">—</span>')
    
    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=303)


# =============================================================================
# Account Approval Management
# =============================================================================

async def get_approval_settings(db) -> dict:
    """Get account approval settings from config."""
    config = await get_config_dict(db)
    return {
        "require_account_approval": config.get(ConfigKeys.REQUIRE_ACCOUNT_APPROVAL, "false") == "true",
        "require_workspace_create_approval": config.get(ConfigKeys.REQUIRE_WORKSPACE_CREATE_APPROVAL, "false") == "true",
    }


@router.get("/pending-approvals", response_class=HTMLResponse)
async def admin_pending_approvals(
    request: Request,
    user: CurrentUser,
    db: DBSession,
):
    """List users pending approval."""
    await require_platform_admin(user)
    
    # Get pending users (not approved)
    result = await db.execute(
        select(User)
        .where(User.is_approved == False)
        .order_by(User.created_at.desc())
    )
    pending_users = result.scalars().all()
    
    # Get users pending workspace creation approval
    result = await db.execute(
        select(User)
        .where(User.is_approved == True, User.can_create_workspaces == False)
        .order_by(User.created_at.desc())
    )
    pending_workspace_users = result.scalars().all()
    
    # Get approval settings
    approval_settings = await get_approval_settings(db)
    
    return templates.TemplateResponse(
        "admin/pending_approvals.html",
        {
            "request": request,
            "user": user,
            "pending_users": pending_users,
            "pending_workspace_users": pending_workspace_users,
            "approval_settings": approval_settings,
        },
    )


@router.post("/users/{user_id}/approve")
async def approve_user(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    user_id: int,
):
    """Approve a user account."""
    await require_platform_admin(user)
    
    from datetime import datetime, timezone
    
    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    target_user.is_approved = True
    target_user.approved_at = datetime.now(timezone.utc)
    target_user.approved_by_id = user.id
    
    await db.commit()
    
    # TODO: Send notification email to user
    
    if request.headers.get("HX-Request"):
        # Check if request is from user detail page (not pending approvals table)
        hx_target = request.headers.get("HX-Target", "")
        if hx_target == "user-approval":
            # User detail page: return a badge
            return HTMLResponse(
                '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-500/20 text-green-400 border border-green-500/30">Approved</span>'
            )
        # Pending approvals list: return a row that fades out
        return HTMLResponse(
            f'''<tr id="pending-row-{user_id}" class="bg-green-900/20 transition-opacity duration-500" 
                    x-data x-init="setTimeout(() => $el.remove(), 1000)">
                <td colspan="4" class="px-6 py-4 text-center text-green-400">
                    <div class="flex items-center justify-center">
                        <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
                        </svg>
                        {target_user.display_name} has been approved
                    </div>
                </td>
            </tr>'''
        )
    
    return RedirectResponse(url="/admin/pending-approvals", status_code=303)


@router.post("/users/{user_id}/reject")
async def reject_user(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    user_id: int,
):
    """Reject and delete a user account."""
    await require_platform_admin(user)
    
    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Can't reject yourself
    if target_user.id == user.id:
        raise HTTPException(status_code=400, detail="Cannot reject yourself")
    
    display_name = target_user.display_name
    
    # Delete the user
    await db.delete(target_user)
    await db.commit()
    
    if request.headers.get("HX-Request"):
        # Return a row that fades out and removes itself
        return HTMLResponse(
            f'''<tr id="pending-row-{user_id}" class="bg-red-900/20 transition-opacity duration-500" 
                    x-data x-init="setTimeout(() => $el.remove(), 1000)">
                <td colspan="4" class="px-6 py-4 text-center text-red-400">
                    <div class="flex items-center justify-center">
                        <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                        </svg>
                        {display_name} has been rejected
                    </div>
                </td>
            </tr>'''
        )
    
    return RedirectResponse(url="/admin/pending-approvals", status_code=303)


@router.post("/users/{user_id}/toggle-workspace-create")
async def toggle_workspace_create(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    user_id: int,
):
    """Toggle user's ability to create workspaces."""
    await require_platform_admin(user)
    
    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    target_user.can_create_workspaces = not target_user.can_create_workspaces
    await db.commit()
    
    if request.headers.get("HX-Request"):
        # Check if request is from user detail page
        hx_target = request.headers.get("HX-Target", "")
        if hx_target == "user-workspace-perm":
            # User detail page
            if target_user.can_create_workspaces:
                return HTMLResponse(
                    '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-500/20 text-blue-400 border border-blue-500/30">Can Create Workspaces</span>'
                )
            return HTMLResponse('')  # No badge when permission is revoked
        
        # Pending approvals page
        if target_user.can_create_workspaces:
            return HTMLResponse(
                '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-500/20 text-green-400">Yes</span>'
            )
        return HTMLResponse(
            '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-yellow-500/20 text-yellow-400">No</span>'
        )
    
    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=303)


@router.get("/config/approvals", response_class=HTMLResponse)
async def admin_approvals_config(
    request: Request,
    user: CurrentUser,
    db: DBSession,
):
    """Account approval settings page."""
    await require_platform_admin(user)
    
    config = await get_config_dict(db)
    
    return templates.TemplateResponse(
        "admin/approvals_config.html",
        {
            "request": request,
            "user": user,
            "config": config,
            "require_account_approval": config.get(ConfigKeys.REQUIRE_ACCOUNT_APPROVAL, "false") == "true",
            "require_workspace_create_approval": config.get(ConfigKeys.REQUIRE_WORKSPACE_CREATE_APPROVAL, "false") == "true",
        },
    )


@router.post("/config/approvals")
async def save_approvals_config(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    require_account_approval: Annotated[str | None, Form()] = None,
    require_workspace_create_approval: Annotated[str | None, Form()] = None,
):
    """Save account approval settings."""
    await require_platform_admin(user)
    
    # Save each config value
    await set_config(
        db, 
        ConfigKeys.REQUIRE_ACCOUNT_APPROVAL, 
        "true" if require_account_approval else "false",
        user.id
    )
    await set_config(
        db, 
        ConfigKeys.REQUIRE_WORKSPACE_CREATE_APPROVAL, 
        "true" if require_workspace_create_approval else "false",
        user.id
    )
    
    await db.commit()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            '''<div id="success-message" class="mb-6 bg-green-500/20 border border-green-500/50 text-green-400 px-4 py-3 rounded-lg">
                <div class="flex items-center">
                    <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
                    </svg>
                    <span>Approval settings saved successfully!</span>
                </div>
            </div>'''
        )
    
    return RedirectResponse(url="/admin/config/approvals", status_code=303)


@router.get("/workspaces", response_class=HTMLResponse)
async def admin_workspaces(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    page: int = Query(default=1, ge=1),
):
    """List all workspaces."""
    await require_platform_admin(user)
    
    per_page = 25
    offset = (page - 1) * per_page
    
    total = await db.scalar(select(func.count()).select_from(Workspace)) or 0
    total_pages = (total + per_page - 1) // per_page
    
    result = await db.execute(
        select(Workspace).order_by(Workspace.created_at.desc()).offset(offset).limit(per_page)
    )
    workspaces = result.scalars().all()
    
    return templates.TemplateResponse(
        "admin/workspaces.html",
        {
            "request": request,
            "user": user,
            "workspaces": workspaces,
            "page": page,
            "total_pages": total_pages,
            "total": total,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def admin_settings(
    request: Request,
    user: CurrentUser,
):
    """View platform settings."""
    await require_platform_admin(user)
    
    return templates.TemplateResponse(
        "admin/settings.html",
        {
            "request": request,
            "user": user,
            "settings": {
                "registration_mode": settings.registration_mode,
                "platform_admin_emails": settings.platform_admin_emails,
                "brand_name": settings.brand_name,
                "brand_company": settings.brand_company,
                "buildly_oauth_enabled": settings.buildly_oauth_enabled,
                "google_oauth_enabled": settings.google_oauth_enabled,
                "push_enabled": settings.push_enabled,
            },
        },
    )


@router.get("/config/branding", response_class=HTMLResponse)
async def admin_branding(
    request: Request,
    user: CurrentUser,
    db: DBSession,
):
    """Branding and theme configuration page."""
    await require_platform_admin(user)
    
    # Get current config from database
    config = await get_config_dict(db)
    
    # Get current brand with overrides
    brand = get_brand_with_overrides(config)
    
    # Determine current preset if any match
    current_preset = None
    for preset_id, preset in THEME_PRESETS.items():
        if (config.get("theme_primary_color", brand.primary_color) == preset["primary_color"] and
            config.get("theme_secondary_color", brand.secondary_color) == preset["secondary_color"] and
            config.get("theme_accent_color", brand.accent_color) == preset["accent_color"]):
            current_preset = preset_id
            break
    
    return templates.TemplateResponse(
        "admin/branding.html",
        {
            "request": request,
            "user": user,
            "brand": brand,
            "config": config,
            "theme_presets": THEME_PRESETS,
            "current_preset": current_preset,
        },
    )


@router.post("/config/branding")
async def save_branding(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    brand_name: Annotated[str | None, Form()] = None,
    brand_company: Annotated[str | None, Form()] = None,
    brand_logo_url: Annotated[str | None, Form()] = None,
    brand_support_email: Annotated[str | None, Form()] = None,
    theme_primary_color: Annotated[str | None, Form()] = None,
    theme_secondary_color: Annotated[str | None, Form()] = None,
    theme_accent_color: Annotated[str | None, Form()] = None,
    theme_dark_mode_default: Annotated[str | None, Form()] = None,
):
    """Save branding configuration."""
    await require_platform_admin(user)
    
    # Save each config value
    configs = {
        ConfigKeys.BRAND_NAME: brand_name,
        ConfigKeys.BRAND_COMPANY: brand_company,
        ConfigKeys.BRAND_LOGO_URL: brand_logo_url,
        ConfigKeys.BRAND_SUPPORT_EMAIL: brand_support_email,
        ConfigKeys.THEME_PRIMARY_COLOR: theme_primary_color,
        ConfigKeys.THEME_SECONDARY_COLOR: theme_secondary_color,
        ConfigKeys.THEME_ACCENT_COLOR: theme_accent_color,
        ConfigKeys.THEME_DARK_MODE_DEFAULT: "true" if theme_dark_mode_default else "false",
    }
    
    for key, value in configs.items():
        await set_config(db, key, value if value else None, user.id)
    
    await db.commit()
    
    # Clear brand cache to pick up new values
    clear_brand_cache()
    
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            '''<div id="success-message" class="mb-6 bg-green-500/20 border border-green-500/50 text-green-400 px-4 py-3 rounded-lg">
                <div class="flex items-center">
                    <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
                    </svg>
                    <span>Settings saved successfully! Refresh to see changes.</span>
                </div>
            </div>'''
        )
    
    return RedirectResponse(url="/admin/config/branding", status_code=303)

