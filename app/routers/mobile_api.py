"""
Mobile JSON API router for native Apple app (iOS/macOS).

Provides token-authenticated JSON endpoints for:
- Auth (login, register, session management)
- Workspaces & channels
- Messages (CRUD, DMs, threads)
- User profiles, status, contacts
- Push token registration (APNs)

All endpoints use Bearer token auth via the Authorization header.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import async_session_maker, get_db
from app.deps import DBSession
from app.models.channel import Channel
from app.models.membership import ChannelMembership, Membership, MembershipRole
from app.models.message import Message
from app.models.reaction import MessageReaction
from app.models.user import AuthProvider, User
from app.models.user_session import UserSession
from app.models.workspace import Workspace
from app.models.bridged_channel import BridgedChannel
from app.services.password import hash_password, validate_password, verify_password
from app.services.rate_limiter import auth_rate_limiter
from app.settings import settings

router = APIRouter(prefix="/mobile/v1", tags=["mobile"])

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

http_bearer = HTTPBearer(auto_error=False)


async def get_mobile_user(
    request: Request,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(http_bearer)] = None,
) -> User:
    """Authenticate via Bearer token (session_token)."""
    token = None
    # Also accept Token-style header for flexibility
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    elif auth_header.startswith("Token "):
        token = auth_header[6:]
    elif bearer:
        token = bearer.credentials

    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")

    async with async_session_maker() as db:
        result = await db.execute(
            select(UserSession)
            .where(UserSession.session_token == token)
            .options(selectinload(UserSession.user))
        )
        session = result.scalar_one_or_none()
        if not session or not session.is_valid() or not session.user or not session.user.is_active:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        session.last_used_at = datetime.now(timezone.utc)
        session.user.update_last_seen()
        await db.commit()
        return session.user


MobileUser = Annotated[User, Depends(get_mobile_user)]

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str
    device_name: str | None = None


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str
    device_name: str | None = None


class AuthResponse(BaseModel):
    token: str
    user: "UserResponse"


class UserResponse(BaseModel):
    id: int
    email: str
    display_name: str
    bio: str | None = None
    title: str | None = None
    phone: str | None = None
    avatar_url: str | None = None
    status: str = "active"
    status_message: str | None = None
    github_url: str | None = None
    linkedin_url: str | None = None
    twitter_url: str | None = None
    website_url: str | None = None
    last_seen_at: datetime | None = None

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            bio=user.bio,
            title=user.title,
            phone=user.phone,
            avatar_url=user.avatar_url,
            status=user.status or "active",
            status_message=user.status_message,
            github_url=user.github_url,
            linkedin_url=user.linkedin_url,
            twitter_url=user.twitter_url,
            website_url=user.website_url,
            last_seen_at=user.last_seen_at,
        )


class UpdateProfileRequest(BaseModel):
    display_name: str | None = None
    bio: str | None = None
    title: str | None = None
    phone: str | None = None
    avatar_url: str | None = None
    status: str | None = None
    status_message: str | None = None
    github_url: str | None = None
    linkedin_url: str | None = None


class WorkspaceResponse(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None = None
    icon_url: str | None = None
    member_count: int = 0
    created_at: datetime | None = None


class ChannelResponse(BaseModel):
    id: int
    workspace_id: int
    name: str
    display_name: str
    description: str | None = None
    topic: str | None = None
    is_private: bool = False
    is_dm: bool = False
    is_archived: bool = False
    unread_count: int = 0
    last_message_at: datetime | None = None
    members: list["UserResponse"] | None = None


class ReactionSummary(BaseModel):
    emoji: str
    count: int
    reacted_by_me: bool = False


class MessageResponse(BaseModel):
    id: int
    channel_id: int
    user_id: int | None = None
    body: str
    parent_id: int | None = None
    thread_reply_count: int = 0
    created_at: datetime
    edited_at: datetime | None = None
    is_edited: bool = False
    external_source: str | None = None
    external_author_name: str | None = None
    author: UserResponse | None = None
    reactions: list[ReactionSummary] = []


class SendMessageRequest(BaseModel):
    body: str
    parent_id: int | None = None


class ConversationPreview(BaseModel):
    """A conversation (DM channel) for the inbox list."""
    channel_id: int
    workspace_id: int
    workspace_name: str
    name: str
    is_dm: bool
    last_message: MessageResponse | None = None
    unread_count: int = 0
    members: list[UserResponse] = []
    bridged_platform: str | None = None  # "slack" or "discord" if bridged


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


@router.post("/auth/login", response_model=AuthResponse)
async def mobile_login(request: Request, body: LoginRequest, db: DBSession):
    """Authenticate and return a Bearer token."""
    client_ip = request.client.host if request.client else "unknown"
    if not auth_rate_limiter.is_allowed(f"login:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many login attempts")

    result = await db.execute(
        select(User).where(User.email == body.email.lower().strip())
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.hashed_password:
        # Account was created via Google (or another SSO) — no password is set.
        raise HTTPException(
            status_code=401,
            detail="This account uses Google sign-in. Please use the 'Sign in with Google' button."
        )
    if not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    session = UserSession.create_session(user_id=user.id, request=request, is_pwa=False)
    if body.device_name:
        session.device_name = body.device_name
        session.device_type = "mobile"
    db.add(session)
    user.update_last_seen()
    await db.commit()

    return AuthResponse(
        token=session.session_token,
        user=UserResponse.from_user(user),
    )


@router.post("/auth/register", response_model=AuthResponse)
async def mobile_register(request: Request, body: RegisterRequest, db: DBSession):
    """Register a new account and return a Bearer token."""
    email = body.email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    errors = validate_password(body.password)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    # Check approval settings
    from app.routers.auth import get_approval_defaults
    approval_defaults = await get_approval_defaults(db)

    user = User(
        email=email,
        display_name=body.display_name.strip(),
        hashed_password=hash_password(body.password),
        auth_provider=AuthProvider.LOCAL,
        **approval_defaults,
    )
    db.add(user)
    await db.flush()

    session = UserSession.create_session(user_id=user.id, request=request, is_pwa=False)
    if body.device_name:
        session.device_name = body.device_name
        session.device_type = "mobile"
    db.add(session)
    await db.commit()
    await db.refresh(user)

    return AuthResponse(
        token=session.session_token,
        user=UserResponse.from_user(user),
    )


@router.post("/auth/logout", status_code=204)
async def mobile_logout(request: Request, user: MobileUser):
    """Revoke the current session token."""
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "").replace("Token ", "")
    async with async_session_maker() as db:
        result = await db.execute(
            select(UserSession).where(UserSession.session_token == token)
        )
        session = result.scalar_one_or_none()
        if session:
            await db.delete(session)
            await db.commit()


# ---------------------------------------------------------------------------
# Google OAuth for native apps
# ---------------------------------------------------------------------------


class OAuthStartResponse(BaseModel):
    auth_url: str
    state: str


class OAuthTokenRequest(BaseModel):
    provider: str = "google"
    code: str
    state: str
    redirect_uri: str


@router.get("/auth/oauth/{provider}/start", response_model=OAuthStartResponse)
async def mobile_oauth_start(request: Request, provider: str):
    """Return the OAuth authorization URL for the native app to open in a browser."""
    from app.services.auth_providers import get_oauth_provider
    from urllib.parse import urlencode
    import secrets

    oauth_provider = get_oauth_provider(provider)
    if not oauth_provider:
        raise HTTPException(status_code=400, detail=f"OAuth provider '{provider}' not available")

    state = secrets.token_urlsafe(32)
    # Use settings.base_url so the URI is correct even behind a reverse proxy.
    # request.base_url would return http://localhost:8000 in that case.
    redirect_uri = settings.base_url.rstrip("/") + f"/mobile/v1/auth/oauth/{provider}/callback"

    params = oauth_provider.get_authorization_params(state)
    # Override the redirect_uri to point to our mobile callback
    params["redirect_uri"] = redirect_uri
    # urlencode is required — scope has spaces and redirect_uri has special chars
    auth_url = f"{oauth_provider.authorization_url}?{urlencode(params)}"

    return OAuthStartResponse(auth_url=auth_url, state=state)


@router.get("/auth/oauth/{provider}/callback")
async def mobile_oauth_callback(
    request: Request,
    db: DBSession,
    provider: str,
    code: str | None = Query(default=None),
    state: str = Query(default=""),
    error: str | None = Query(default=None),
):
    """Handle OAuth callback for native apps — returns JSON with token."""
    from app.services.auth_providers import get_oauth_provider

    if error:
        raise HTTPException(status_code=400, detail=f"OAuth denied: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    oauth_provider = get_oauth_provider(provider)
    if not oauth_provider:
        raise HTTPException(status_code=400, detail=f"OAuth provider '{provider}' not available")

    # Override redirect_uri to match what we sent during authorization
    redirect_uri = settings.base_url.rstrip("/") + f"/mobile/v1/auth/oauth/{provider}/callback"

    try:
        tokens = await oauth_provider.exchange_code(code, redirect_uri=redirect_uri)
        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token")
        user_info = await oauth_provider.get_user_info(access_token)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth exchange failed: {e}")

    # Find or create user
    result = await db.execute(
        select(User).where(
            (User.email == user_info.email) |
            ((User.auth_provider == provider) & (User.provider_sub == user_info.sub))
        )
    )
    user = result.scalar_one_or_none()

    if user:
        user.provider_sub = user_info.sub
        if user_info.picture:
            user.avatar_url = user_info.picture
        if provider == "google" and refresh_token:
            user.google_refresh_token = refresh_token
    else:
        from app.routers.auth import get_approval_defaults
        approval_defaults = await get_approval_defaults(db)
        is_admin = settings.is_admin_email(user_info.email)

        user = User(
            email=user_info.email,
            display_name=user_info.name or user_info.email.split("@")[0],
            auth_provider=AuthProvider(provider),
            provider_sub=user_info.sub,
            avatar_url=user_info.picture,
            is_platform_admin=is_admin,
            is_approved=True if is_admin else approval_defaults["is_approved"],
            can_create_workspaces=True if is_admin else approval_defaults["can_create_workspaces"],
        )
        if provider == "google" and refresh_token:
            user.google_refresh_token = refresh_token
        db.add(user)
        await db.flush()

    session = UserSession.create_session(user_id=user.id, request=request, is_pwa=False)
    session.device_type = "mobile"
    session.device_name = "Native App"
    db.add(session)
    user.update_last_seen()
    await db.commit()

    # Redirect to the native app via custom URL scheme so ASWebAuthenticationSession
    # can intercept it and hand the token back to the app.
    from fastapi.responses import RedirectResponse as FastRedirect
    from urllib.parse import quote
    token = session.session_token
    return FastRedirect(
        url=f"forge://oauth?token={quote(token, safe='')}",
        status_code=302,
    )


@router.get("/me", response_model=UserResponse)
async def get_my_profile(user: MobileUser):
    """Get the authenticated user's profile."""
    return UserResponse.from_user(user)


@router.patch("/me", response_model=UserResponse)
async def update_my_profile(user: MobileUser, body: UpdateProfileRequest, db: DBSession):
    """Update the authenticated user's profile."""
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(user, field, value)
    # Re-attach user to this session's identity map
    merged = await db.merge(user)
    await db.commit()
    await db.refresh(merged)
    return UserResponse.from_user(merged)


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, user: MobileUser, db: DBSession):
    """Get a user's public profile."""
    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.from_user(target)


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------


@router.get("/workspaces", response_model=list[WorkspaceResponse])
async def list_workspaces(user: MobileUser, db: DBSession):
    """List workspaces the user belongs to."""
    result = await db.execute(
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
        .order_by(Workspace.name)
    )
    workspaces = result.scalars().all()
    out = []
    for ws in workspaces:
        count_result = await db.execute(
            select(func.count()).select_from(Membership).where(Membership.workspace_id == ws.id)
        )
        out.append(WorkspaceResponse(
            id=ws.id, name=ws.name, slug=ws.slug,
            description=ws.description, icon_url=ws.icon_url,
            member_count=count_result.scalar() or 0,
            created_at=ws.created_at,
        ))
    return out


@router.get("/workspaces/{workspace_id}/members", response_model=list[UserResponse])
async def list_workspace_members(workspace_id: int, user: MobileUser, db: DBSession):
    """List members of a workspace."""
    # Verify membership
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member")

    result = await db.execute(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.workspace_id == workspace_id, User.is_active == True)
        .order_by(User.display_name)
    )
    return [UserResponse.from_user(u) for u in result.scalars().all()]


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/channels", response_model=list[ChannelResponse])
async def list_channels(workspace_id: int, user: MobileUser, db: DBSession):
    """List channels in a workspace the user can see."""
    # Verify workspace membership
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member")

    # Public channels + private channels user is a member of
    result = await db.execute(
        select(Channel)
        .outerjoin(
            ChannelMembership,
            and_(ChannelMembership.channel_id == Channel.id, ChannelMembership.user_id == user.id),
        )
        .where(
            Channel.workspace_id == workspace_id,
            Channel.is_archived == False,
            or_(Channel.is_private == False, ChannelMembership.id != None),
        )
        .order_by(Channel.is_dm, Channel.name)
    )
    channels = result.scalars().unique().all()

    out = []
    for ch in channels:
        # Unread count
        cm_result = await db.execute(
            select(ChannelMembership).where(
                ChannelMembership.channel_id == ch.id,
                ChannelMembership.user_id == user.id,
            )
        )
        cm = cm_result.scalar_one_or_none()
        unread = 0
        if cm is not None:
            unread_filters = [
                Message.channel_id == ch.id,
                Message.user_id.is_distinct_from(user.id),
                Message.deleted_at == None,
                Message.parent_id == None,  # top-level only, matches web sidebar
            ]
            if cm.last_read_message_id is not None:
                unread_filters.append(Message.id > cm.last_read_message_id)
            unread_result = await db.execute(
                select(func.count()).select_from(Message).where(*unread_filters)
            )
            unread = unread_result.scalar() or 0

        # Last message time
        last_msg_result = await db.execute(
            select(Message.created_at)
            .where(Message.channel_id == ch.id, Message.deleted_at == None)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_msg_at = last_msg_result.scalar_one_or_none()

        display_name = ch.display_name if hasattr(ch, "display_name") else ch.name

        out.append(ChannelResponse(
            id=ch.id, workspace_id=ch.workspace_id,
            name=ch.name, display_name=display_name,
            description=ch.description, topic=ch.topic,
            is_private=ch.is_private, is_dm=ch.is_dm,
            is_archived=ch.is_archived,
            unread_count=unread,
            last_message_at=last_msg_at,
        ))
    return out


# ---------------------------------------------------------------------------
# Conversations (DM inbox — across all workspaces)
# ---------------------------------------------------------------------------


@router.get("/conversations", response_model=list[ConversationPreview])
async def list_conversations(
    user: MobileUser,
    db: DBSession,
    include_channels: bool = Query(False, description="Include non-DM channels"),
):
    """List DM conversations (and optionally channels) across all workspaces.

    This is the Telegram-style inbox view. DMs from Slack/Discord bridged
    channels also appear here.
    """
    # Get all channels the user is a member of
    query = (
        select(Channel, Workspace.name.label("ws_name"))
        .join(Workspace, Workspace.id == Channel.workspace_id)
        .join(ChannelMembership, ChannelMembership.channel_id == Channel.id)
        .where(
            ChannelMembership.user_id == user.id,
            Channel.is_archived == False,
        )
    )
    if not include_channels:
        query = query.where(Channel.is_dm == True)

    query = query.order_by(Channel.name)
    result = await db.execute(query)
    rows = result.all()

    # Look up which channels are bridged to Slack/Discord
    channel_ids = [ch.id for ch, _ in rows]
    bridge_result = await db.execute(
        select(BridgedChannel.channel_id, BridgedChannel.platform)
        .where(BridgedChannel.channel_id.in_(channel_ids), BridgedChannel.is_active == True)
    )
    bridge_map = {row.channel_id: row.platform for row in bridge_result.all()}

    previews = []
    for ch, ws_name in rows:
        # Last message
        msg_result = await db.execute(
            select(Message)
            .where(Message.channel_id == ch.id, Message.deleted_at == None)
            .options(selectinload(Message.user))
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_msg = msg_result.scalar_one_or_none()

        last_msg_resp = None
        if last_msg:
            last_msg_resp = MessageResponse(
                id=last_msg.id, channel_id=last_msg.channel_id,
                user_id=last_msg.user_id, body=last_msg.body,
                parent_id=last_msg.parent_id,
                thread_reply_count=last_msg.thread_reply_count or 0,
                created_at=last_msg.created_at,
                edited_at=last_msg.edited_at,
                is_edited=last_msg.is_edited if hasattr(last_msg, "is_edited") else False,
                external_source=last_msg.external_source,
                external_author_name=last_msg.external_author_name,
                author=UserResponse.from_user(last_msg.user) if last_msg.user else None,
            )

        # Unread count
        cm_result = await db.execute(
            select(ChannelMembership).where(
                ChannelMembership.channel_id == ch.id,
                ChannelMembership.user_id == user.id,
            )
        )
        cm = cm_result.scalar_one_or_none()
        unread = 0
        if cm is not None:
            unread_filters = [
                Message.channel_id == ch.id,
                Message.user_id.is_distinct_from(user.id),
                Message.deleted_at == None,
                Message.parent_id == None,  # top-level only, matches web sidebar
            ]
            if cm.last_read_message_id is not None:
                unread_filters.append(Message.id > cm.last_read_message_id)
            unread_result = await db.execute(
                select(func.count()).select_from(Message).where(*unread_filters)
            )
            unread = unread_result.scalar() or 0

        # DM members
        members_result = await db.execute(
            select(User)
            .join(ChannelMembership, ChannelMembership.user_id == User.id)
            .where(ChannelMembership.channel_id == ch.id, User.is_active == True)
        )
        members = [UserResponse.from_user(u) for u in members_result.scalars().all()]

        display_name = ch.display_name if hasattr(ch, "display_name") else ch.name

        # Legacy Slack-synced channels are named "SLACK:<name>" without a
        # BridgedChannel row — treat them as bridged so clients can group
        # them into a Slack folder, and strip the prefix for display.
        bridged_platform = bridge_map.get(ch.id)
        if bridged_platform is None and ch.name.upper().startswith("SLACK:"):
            bridged_platform = "slack"
        if display_name.upper().startswith("SLACK:"):
            display_name = display_name[6:].strip() or display_name

        previews.append(ConversationPreview(
            channel_id=ch.id,
            workspace_id=ch.workspace_id,
            workspace_name=ws_name,
            name=display_name,
            is_dm=ch.is_dm,
            last_message=last_msg_resp,
            unread_count=unread,
            members=members,
            bridged_platform=bridged_platform,
        ))

    # Sort by last message time (most recent first)
    previews.sort(
        key=lambda p: p.last_message.created_at if p.last_message else datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return previews


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def _load_reactions(db, message_ids: list[int], current_user_id: int) -> dict[int, list[ReactionSummary]]:
    """Batch-load reactions grouped by emoji for a list of message IDs."""
    if not message_ids:
        return {}
    result = await db.execute(
        select(MessageReaction).where(MessageReaction.message_id.in_(message_ids))
    )
    all_reactions = result.scalars().all()

    grouped: dict[int, dict[str, dict]] = {}
    for r in all_reactions:
        bucket = grouped.setdefault(r.message_id, {})
        entry = bucket.setdefault(r.emoji, {"count": 0, "user_ids": set()})
        entry["count"] += 1
        entry["user_ids"].add(r.user_id)

    return {
        msg_id: [
            ReactionSummary(emoji=emoji, count=data["count"], reacted_by_me=current_user_id in data["user_ids"])
            for emoji, data in emojis.items()
        ]
        for msg_id, emojis in grouped.items()
    }


@router.get(
    "/workspaces/{workspace_id}/channels/{channel_id}/messages",
    response_model=list[MessageResponse],
)
async def list_messages(
    workspace_id: int,
    channel_id: int,
    user: MobileUser,
    db: DBSession,
    before: int | None = Query(None, description="Message ID to fetch before (pagination)"),
    after: int | None = Query(None, description="Message ID to fetch after (catch-up)"),
    limit: int = Query(50, ge=1, le=100),
):
    """List messages in a channel (paginated, newest last)."""
    # Verify access
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member")

    query = (
        select(Message)
        .where(
            Message.channel_id == channel_id,
            Message.deleted_at == None,
            Message.parent_id == None,  # top-level only
        )
        .options(selectinload(Message.user))
    )
    if before:
        query = query.where(Message.id < before)
    if after:
        query = query.where(Message.id > after)

    # Order by id, not created_at: bridged/external messages can carry a
    # backdated created_at from the original platform, which desynced the
    # WHERE-by-id cursor from the ORDER-by-created_at result set and made
    # messages render out of order (or drop out of the LIMIT window) in the
    # native app.
    query = query.order_by(Message.id.desc()).limit(limit)
    result = await db.execute(query)
    messages = list(reversed(result.scalars().all()))

    reactions_map = await _load_reactions(db, [m.id for m in messages], user.id)

    return [
        MessageResponse(
            id=m.id, channel_id=m.channel_id, user_id=m.user_id,
            body=m.body, parent_id=m.parent_id,
            thread_reply_count=m.thread_reply_count or 0,
            created_at=m.created_at, edited_at=m.edited_at,
            is_edited=m.is_edited if hasattr(m, "is_edited") else False,
            external_source=m.external_source,
            external_author_name=m.external_author_name,
            author=UserResponse.from_user(m.user) if m.user else None,
            reactions=reactions_map.get(m.id, []),
        )
        for m in messages
    ]


@router.get(
    "/workspaces/{workspace_id}/channels/{channel_id}/messages/{message_id}/thread",
    response_model=list[MessageResponse],
)
async def get_thread(
    workspace_id: int, channel_id: int, message_id: int,
    user: MobileUser, db: DBSession,
):
    """Get a thread (parent + replies)."""
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member")

    # Parent
    result = await db.execute(
        select(Message)
        .where(Message.id == message_id, Message.deleted_at == None)
        .options(selectinload(Message.user))
    )
    parent = result.scalar_one_or_none()
    if not parent:
        raise HTTPException(status_code=404, detail="Message not found")

    # Replies
    result = await db.execute(
        select(Message)
        .where(Message.parent_id == message_id, Message.deleted_at == None)
        .options(selectinload(Message.user))
        .order_by(Message.created_at.asc())
    )
    replies = result.scalars().all()

    def to_resp(m):
        return MessageResponse(
            id=m.id, channel_id=m.channel_id, user_id=m.user_id,
            body=m.body, parent_id=m.parent_id,
            thread_reply_count=m.thread_reply_count or 0,
            created_at=m.created_at, edited_at=m.edited_at,
            is_edited=m.is_edited if hasattr(m, "is_edited") else False,
            external_source=m.external_source,
            external_author_name=m.external_author_name,
            author=UserResponse.from_user(m.user) if m.user else None,
        )

    return [to_resp(parent)] + [to_resp(r) for r in replies]


@router.post(
    "/workspaces/{workspace_id}/channels/{channel_id}/messages",
    response_model=MessageResponse,
    status_code=201,
)
async def send_message(
    workspace_id: int, channel_id: int,
    user: MobileUser, body: SendMessageRequest, db: DBSession,
):
    """Send a message in a channel."""
    # Verify membership
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member")

    # Verify channel exists in workspace
    result = await db.execute(
        select(Channel).where(Channel.id == channel_id, Channel.workspace_id == workspace_id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    msg = Message(
        channel_id=channel_id,
        user_id=user.id,
        body=body.body.strip(),
        parent_id=body.parent_id,
    )
    db.add(msg)

    # Update thread reply count if it's a reply
    if body.parent_id:
        result = await db.execute(
            select(Message).where(Message.id == body.parent_id)
        )
        parent = result.scalar_one_or_none()
        if parent:
            parent.thread_reply_count = (parent.thread_reply_count or 0) + 1

    await db.commit()
    await db.refresh(msg, attribute_names=["user"])

    # Broadcast via WebSocket (fire and forget)
    # The existing WS system sends HTML; native clients will get updates
    # via their own WS connection or push notifications
    try:
        from app.routers.realtime import broadcast_new_message
        # We pass empty HTML since the WS broadcast is HTML-based;
        # native clients re-fetch via the JSON API
        await broadcast_new_message(
            channel_id=channel_id,
            message_html="",
            message_id=msg.id,
            user_id=user.id,
            user_name=user.display_name,
            parent_id=body.parent_id,
        )
    except Exception:
        pass  # Don't fail the API call if WS broadcast fails

    return MessageResponse(
        id=msg.id, channel_id=msg.channel_id, user_id=msg.user_id,
        body=msg.body, parent_id=msg.parent_id,
        thread_reply_count=msg.thread_reply_count or 0,
        created_at=msg.created_at, edited_at=msg.edited_at,
        is_edited=False,
        author=UserResponse.from_user(user),
    )


@router.put(
    "/workspaces/{workspace_id}/channels/{channel_id}/messages/{message_id}",
    response_model=MessageResponse,
)
async def edit_message(
    workspace_id: int, channel_id: int, message_id: int,
    user: MobileUser, body: SendMessageRequest, db: DBSession,
):
    """Edit a message (only the author can edit)."""
    result = await db.execute(
        select(Message)
        .where(Message.id == message_id, Message.channel_id == channel_id, Message.deleted_at == None)
        .options(selectinload(Message.user))
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.user_id != user.id:
        raise HTTPException(status_code=403, detail="Can only edit your own messages")

    msg.body = body.body.strip()
    msg.edited_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(msg)

    return MessageResponse(
        id=msg.id, channel_id=msg.channel_id, user_id=msg.user_id,
        body=msg.body, parent_id=msg.parent_id,
        thread_reply_count=msg.thread_reply_count or 0,
        created_at=msg.created_at, edited_at=msg.edited_at,
        is_edited=True,
        author=UserResponse.from_user(user),
    )


@router.delete(
    "/workspaces/{workspace_id}/channels/{channel_id}/messages/{message_id}",
    status_code=204,
)
async def delete_message(
    workspace_id: int, channel_id: int, message_id: int,
    user: MobileUser, db: DBSession,
):
    """Soft-delete a message (author or workspace admin)."""
    result = await db.execute(
        select(Message).where(
            Message.id == message_id, Message.channel_id == channel_id, Message.deleted_at == None,
        )
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    # Check if author or workspace admin
    if msg.user_id != user.id:
        result = await db.execute(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user.id,
                Membership.role.in_([MembershipRole.ADMIN, MembershipRole.OWNER]),
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Not authorized")

    msg.deleted_at = datetime.now(timezone.utc)
    await db.commit()


@router.post(
    "/workspaces/{workspace_id}/channels/{channel_id}/read",
    status_code=204,
)
async def mark_channel_read(
    workspace_id: int, channel_id: int,
    user: MobileUser, db: DBSession,
):
    """Mark a channel as read (sets last_read_message_id to the latest message)."""
    result = await db.execute(
        select(ChannelMembership).where(
            ChannelMembership.channel_id == channel_id,
            ChannelMembership.user_id == user.id,
        )
    )
    cm = result.scalar_one_or_none()
    # Get latest message ID (including thread replies)
    latest = await db.execute(
        select(Message.id)
        .where(Message.channel_id == channel_id, Message.deleted_at == None)
        .order_by(Message.id.desc())
        .limit(1)
    )
    latest_id = latest.scalar_one_or_none()
    if latest_id is None:
        return
    if cm:
        if (cm.last_read_message_id or 0) < latest_id:
            cm.last_read_message_id = latest_id
            await db.commit()
    else:
        # No membership row yet (e.g. public channel the user only reads) —
        # create one so the read cursor persists and badges actually clear.
        db.add(ChannelMembership(
            channel_id=channel_id,
            user_id=user.id,
            last_read_message_id=latest_id,
        ))
        await db.commit()


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------


class ToggleReactionRequest(BaseModel):
    emoji: str


@router.post(
    "/workspaces/{workspace_id}/channels/{channel_id}/messages/{message_id}/reactions/toggle",
    response_model=list[ReactionSummary],
)
async def toggle_reaction(
    workspace_id: int, channel_id: int, message_id: int,
    body: ToggleReactionRequest,
    user: MobileUser, db: DBSession,
):
    """Toggle an emoji reaction on a message. Returns updated reaction list."""
    # Verify membership
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member")

    # Verify message exists in channel
    result = await db.execute(
        select(Message).where(Message.id == message_id, Message.channel_id == channel_id, Message.deleted_at == None)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Message not found")

    # Toggle reaction
    result = await db.execute(
        select(MessageReaction).where(
            MessageReaction.message_id == message_id,
            MessageReaction.user_id == user.id,
            MessageReaction.emoji == body.emoji,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        await db.delete(existing)
    else:
        db.add(MessageReaction(message_id=message_id, user_id=user.id, emoji=body.emoji))
    await db.commit()

    # Return updated reaction list
    reactions_map = await _load_reactions(db, [message_id], user.id)
    return reactions_map.get(message_id, [])


# ---------------------------------------------------------------------------
# DM creation
# ---------------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/dm", response_model=ChannelResponse)
async def create_dm(
    workspace_id: int,
    user: MobileUser,
    db: DBSession,
    user_ids: list[int] = Query(..., description="User IDs to DM"),
):
    """Create or find an existing DM channel with the given users."""
    all_user_ids = sorted(set([user.id] + user_ids))

    # Check if DM already exists with exactly these members
    result = await db.execute(
        select(Channel)
        .join(ChannelMembership, ChannelMembership.channel_id == Channel.id)
        .where(
            Channel.workspace_id == workspace_id,
            Channel.is_dm == True,
        )
        .group_by(Channel.id)
        .having(func.count(ChannelMembership.id) == len(all_user_ids))
    )
    existing_channels = result.scalars().all()

    for ch in existing_channels:
        members_result = await db.execute(
            select(ChannelMembership.user_id).where(ChannelMembership.channel_id == ch.id)
        )
        member_ids = sorted([r[0] for r in members_result.all()])
        if member_ids == all_user_ids:
            display_name = ch.display_name if hasattr(ch, "display_name") else ch.name
            return ChannelResponse(
                id=ch.id, workspace_id=ch.workspace_id,
                name=ch.name, display_name=display_name,
                is_dm=True, is_private=True,
            )

    # Create new DM channel
    # Build name from member display names
    others_result = await db.execute(
        select(User).where(User.id.in_(user_ids))
    )
    others = others_result.scalars().all()
    dm_name = "dm-" + "-".join(str(uid) for uid in all_user_ids)
    display_name = ", ".join(u.display_name for u in others) if others else "Direct Message"

    new_channel = Channel(
        workspace_id=workspace_id,
        name=dm_name,
        description=f"DM with {display_name}",
        is_dm=True,
        is_private=True,
    )
    db.add(new_channel)
    await db.flush()

    for uid in all_user_ids:
        db.add(ChannelMembership(channel_id=new_channel.id, user_id=uid))

    await db.commit()
    await db.refresh(new_channel)

    return ChannelResponse(
        id=new_channel.id, workspace_id=new_channel.workspace_id,
        name=new_channel.name, display_name=display_name,
        is_dm=True, is_private=True,
    )


# ---------------------------------------------------------------------------
# Integrations (Slack / Discord) — mobile endpoints
# ---------------------------------------------------------------------------


class IntegrationStatus(BaseModel):
    slack_connected: bool = False
    slack_workspace: str | None = None
    discord_connected: bool = False
    discord_server: str | None = None


class IntegrationAuthURL(BaseModel):
    url: str


@router.get("/integrations/status", response_model=IntegrationStatus)
async def get_integration_status(user: MobileUser, db: DBSession):
    """Return which integrations the user has connected."""
    from app.models.external_integration import ExternalIntegration, IntegrationType

    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.is_active == True,
        )
    )
    integrations = result.scalars().all()

    status = IntegrationStatus()
    for integ in integrations:
        if integ.integration_type == IntegrationType.SLACK:
            status.slack_connected = True
            status.slack_workspace = integ.external_team_name
        elif integ.integration_type == IntegrationType.DISCORD:
            status.discord_connected = True
            status.discord_server = integ.external_team_name
    return status


@router.get("/integrations/slack/auth-url", response_model=IntegrationAuthURL)
async def get_slack_auth_url(request: Request, user: MobileUser):
    """Return the Slack OAuth consent URL for the native app to open in Safari."""
    from app.services.slack import slack_service
    import secrets

    if not slack_service.is_configured:
        raise HTTPException(status_code=400, detail="Slack integration is not configured on this server")

    proto = request.headers.get("X-Forwarded-Proto", "https" if not settings.debug else "http")
    host = request.headers.get("X-Forwarded-Host", request.url.netloc)
    redirect_uri = f"{proto}://{host}/integrations/slack/callback"

    state = secrets.token_urlsafe(32)
    auth_url = slack_service.get_authorization_url(state, redirect_uri)

    # Store state in a short-lived session-like mechanism
    # The callback will need to handle this — for now return the raw URL
    return IntegrationAuthURL(url=auth_url)


@router.get("/integrations/discord/auth-url", response_model=IntegrationAuthURL)
async def get_discord_auth_url(request: Request, user: MobileUser):
    """Return the Discord OAuth consent URL for the native app to open in Safari."""
    from app.services.discord import discord_service
    import secrets

    if not discord_service.is_configured:
        raise HTTPException(status_code=400, detail="Discord integration is not configured on this server")

    proto = request.headers.get("X-Forwarded-Proto", "https" if not settings.debug else "http")
    host = request.headers.get("X-Forwarded-Host", request.url.netloc)
    redirect_uri = f"{proto}://{host}/integrations/discord/callback"

    state = secrets.token_urlsafe(32)
    auth_url = discord_service.get_authorization_url(state, redirect_uri)

    return IntegrationAuthURL(url=auth_url)


class SyncResult(BaseModel):
    synced: int = 0
    skipped: int = 0
    message: str = ""


@router.post("/integrations/slack/sync", response_model=SyncResult)
async def mobile_slack_sync(
    user: MobileUser,
    db: DBSession,
    workspace_id: int = Query(..., description="Workspace to sync Slack channels into"),
):
    """Sync Slack channels into a Forge workspace, creating BridgedChannel records."""
    from app.models.external_integration import ExternalIntegration, IntegrationType
    from app.models.bridged_channel import BridgePlatform
    from app.services.slack import slack_service

    # Get active Slack integration
    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.integration_type == IntegrationType.SLACK,
            ExternalIntegration.is_active == True,
        )
    )
    integration = result.scalar_one_or_none()
    if not integration or not integration.access_token:
        raise HTTPException(status_code=400, detail="No active Slack integration")

    # Verify workspace membership
    result = await db.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    # Fetch Slack channels (including DMs and group DMs)
    slack_channels = await slack_service.list_channels(
        integration.access_token,
        types="public_channel,private_channel,im,mpim",
    )
    if not slack_channels:
        return SyncResult(message="No Slack channels found")

    # For DMs, we need user info to get display names
    dm_user_ids = [
        sc.get("user")
        for sc in slack_channels
        if sc.get("is_im") and sc.get("user")
    ]
    users_info = {}
    if dm_user_ids:
        users_info = await slack_service.get_users_by_ids(
            integration.access_token, dm_user_ids
        )

    synced = 0
    skipped = 0
    for sc in slack_channels:
        ext_id = sc.get("id")
        is_im = sc.get("is_im", False)
        is_mpim = sc.get("is_mpim", False)
        is_private = sc.get("is_private", False)

        # Only 1:1 IMs are DMs; MPIMs (group DMs) are treated as channels
        is_dm = is_im and not is_mpim

        # Determine display name and skip bots
        if is_im:
            user_info = users_info.get(sc.get("user"), {})
            # Skip bot/app DMs
            if user_info.get("is_bot") or user_info.get("id") == "USLACKBOT":
                skipped += 1
                continue
            profile = user_info.get("profile", {})
            name = (
                profile.get("real_name")
                or user_info.get("real_name")
                or user_info.get("name")
                or "DM"
            )
        elif is_mpim:
            # Format group DM name from mpdm-user1--user2--1 to readable form
            raw = sc.get("name", "Group DM")
            if raw.startswith("mpdm-"):
                parts = raw[5:].rsplit("-", 1)[0].split("--")
                name = ", ".join(p.replace(".", " ").title() for p in parts)
            else:
                name = raw
        else:
            name = sc.get("name", "unnamed")

        # Skip already bridged
        result = await db.execute(
            select(BridgedChannel).where(
                BridgedChannel.external_channel_id == ext_id,
                BridgedChannel.integration_id == integration.id,
            )
        )
        if result.scalar_one_or_none():
            skipped += 1
            continue

        # Reuse or create Forge channel
        forge_name = f"SLACK:{name}"
        result = await db.execute(
            select(Channel).where(
                Channel.workspace_id == workspace_id,
                Channel.name == forge_name,
            )
        )
        forge_channel = result.scalar_one_or_none()
        if not forge_channel:
            forge_channel = Channel(
                workspace_id=workspace_id,
                name=forge_name,
                description=f"Synced from Slack: #{name}" if not is_dm else f"Slack DM: {name}",
                is_private=is_private or is_dm,
                is_dm=is_dm,
                is_archived=False,
            )
            db.add(forge_channel)
            await db.flush()
            db.add(ChannelMembership(
                channel_id=forge_channel.id,
                user_id=user.id,
            ))

        db.add(BridgedChannel(
            channel_id=forge_channel.id,
            integration_id=integration.id,
            platform=BridgePlatform.SLACK,
            external_channel_id=ext_id,
            external_channel_name=name,
            sync_incoming=True,
            sync_outgoing=True,
            reply_prefix="From Forge:",
        ))
        synced += 1

    await db.commit()
    msg = f"Synced {synced} Slack channels"
    if skipped:
        msg += f" (skipped {skipped} already bridged)"
    return SyncResult(synced=synced, skipped=skipped, message=msg)


@router.post("/integrations/slack/cleanup", response_model=SyncResult)
async def mobile_slack_cleanup(user: MobileUser, db: DBSession):
    """Fix existing Slack DM data: mark MPIMs as non-DM, remove bot DM channels."""
    from app.models.bridged_channel import BridgedChannel, BridgePlatform
    from app.models.external_integration import ExternalIntegration, IntegrationType
    from app.services.slack import slack_service

    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.integration_type == IntegrationType.SLACK,
            ExternalIntegration.is_active == True,
        )
    )
    integration = result.scalar_one_or_none()
    if not integration or not integration.access_token:
        return SyncResult(message="No active Slack integration")

    # Get all Slack bridges for this integration
    result = await db.execute(
        select(BridgedChannel)
        .where(
            BridgedChannel.integration_id == integration.id,
            BridgedChannel.platform == BridgePlatform.SLACK,
        )
    )
    bridges = result.scalars().all()

    # Get Slack conversations to check types
    slack_channels = await slack_service.list_channels(
        integration.access_token,
        types="public_channel,private_channel,im,mpim",
    )
    slack_map = {ch.get("id"): ch for ch in slack_channels}

    # Get user info for IM user IDs (to detect bots)
    im_user_ids = [
        ch.get("user")
        for ch in slack_channels
        if ch.get("is_im") and ch.get("user")
    ]
    users_info = {}
    if im_user_ids:
        users_info = await slack_service.get_users_by_ids(
            integration.access_token, im_user_ids
        )

    fixed = 0
    removed = 0
    for bridge in bridges:
        sc = slack_map.get(bridge.external_channel_id)
        if not sc:
            continue

        channel_id = bridge.channel_id
        channel_result = await db.execute(
            select(Channel).where(Channel.id == channel_id)
        )
        channel = channel_result.scalar_one_or_none()
        if not channel:
            continue

        is_im = sc.get("is_im", False)
        is_mpim = sc.get("is_mpim", False)

        # Fix MPIMs: should be channels, not DMs
        if is_mpim and channel.is_dm:
            channel.is_dm = False
            # Clean up name
            raw = sc.get("name", "")
            if raw.startswith("mpdm-"):
                parts = raw[5:].rsplit("-", 1)[0].split("--")
                clean_name = ", ".join(p.replace(".", " ").title() for p in parts)
                channel.name = f"SLACK:{clean_name}"
            fixed += 1

        # Remove bot DM channels
        if is_im:
            user_info = users_info.get(sc.get("user"), {})
            if user_info.get("is_bot") or user_info.get("id") == "USLACKBOT":
                # Delete bridge and channel
                await db.delete(bridge)
                # Delete channel memberships first
                await db.execute(
                    ChannelMembership.__table__.delete().where(
                        ChannelMembership.channel_id == channel_id
                    )
                )
                await db.delete(channel)
                removed += 1

    await db.commit()
    return SyncResult(
        synced=fixed,
        skipped=removed,
        message=f"Fixed {fixed} MPIMs, removed {removed} bot DMs",
    )


class ImportResult(BaseModel):
    imported: int = 0
    message: str = ""


@router.post("/channels/{channel_id}/import-messages", response_model=ImportResult)
async def mobile_import_messages(
    user: MobileUser,
    db: DBSession,
    channel_id: int,
    limit: int = Query(20, ge=1, le=100),
):
    """Import recent messages from an external bridged channel (e.g. Slack)."""
    from app.models.bridged_channel import BridgedChannel, BridgePlatform
    from app.models.external_integration import ExternalIntegration
    from app.services.slack import slack_service

    # Find bridge for this channel
    result = await db.execute(
        select(BridgedChannel)
        .options(selectinload(BridgedChannel.integration))
        .where(BridgedChannel.channel_id == channel_id)
    )
    bridge = result.scalar_one_or_none()
    if not bridge:
        return ImportResult(message="Not a bridged channel")

    integration = bridge.integration
    if not integration or not integration.access_token:
        return ImportResult(message="Integration not connected")

    # Verify user has access to the channel
    result = await db.execute(
        select(ChannelMembership).where(
            ChannelMembership.channel_id == channel_id,
            ChannelMembership.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a channel member")

    imported_count = 0

    if bridge.platform == BridgePlatform.SLACK:
        messages = await slack_service.get_channel_history(
            integration.access_token,
            bridge.external_channel_id,
            limit=limit,
        )

        # Get user info for messages
        user_ids = list(set(m.get("user") for m in messages if m.get("user")))
        users_info = await slack_service.get_users_by_ids(
            integration.access_token, user_ids
        )

        for msg_data in messages:
            ts = msg_data.get("ts")
            if not ts or msg_data.get("subtype"):
                continue

            # Check if already imported
            result = await db.execute(
                select(Message).where(
                    Message.external_message_id == ts,
                    Message.channel_id == channel_id,
                )
            )
            if result.scalar_one_or_none():
                continue

            user_info = users_info.get(msg_data.get("user"), {})
            profile = user_info.get("profile", {})
            author_name = (
                profile.get("real_name")
                or user_info.get("real_name")
                or user_info.get("name")
                or "Unknown"
            )
            avatar = profile.get("image_48") or profile.get("image_72")

            new_msg = Message(
                channel_id=channel_id,
                body=msg_data.get("text", ""),
                external_source="slack",
                external_message_id=ts,
                external_channel_id=bridge.external_channel_id,
                external_author_name=author_name,
                external_author_avatar=avatar,
                created_at=datetime.fromtimestamp(float(ts), tz=timezone.utc),
            )
            db.add(new_msg)
            imported_count += 1

    if imported_count > 0:
        await db.commit()
        bridge.last_sync_at = datetime.now(timezone.utc)
        bridge.messages_imported = (bridge.messages_imported or 0) + imported_count
        await db.commit()

    return ImportResult(imported=imported_count, message=f"Imported {imported_count} messages")


class SlackContactOut(BaseModel):
    slack_user_id: str
    display_name: str
    real_name: str
    email: str | None = None
    avatar_url: str | None = None
    title: str | None = None
    is_online: bool = False


@router.get("/integrations/slack/contacts", response_model=list[SlackContactOut])
async def mobile_slack_contacts(user: MobileUser, db: DBSession):
    """List contacts from connected Slack workspace."""
    from app.models.external_integration import ExternalIntegration, IntegrationType
    from app.services.slack import slack_service

    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.integration_type == IntegrationType.SLACK,
            ExternalIntegration.is_active == True,
        )
    )
    integration = result.scalar_one_or_none()
    if not integration or not integration.access_token:
        return []

    members = await slack_service.list_users(integration.access_token)
    contacts = []
    for m in members:
        profile = m.get("profile", {})
        contacts.append(SlackContactOut(
            slack_user_id=m.get("id", ""),
            display_name=profile.get("display_name") or m.get("name", ""),
            real_name=profile.get("real_name") or m.get("real_name", ""),
            email=profile.get("email"),
            avatar_url=profile.get("image_72") or profile.get("image_48"),
            title=profile.get("title") or None,
            is_online=m.get("presence") == "active",
        ))
    return contacts


@router.post("/integrations/slack/disconnect")
async def mobile_slack_disconnect(user: MobileUser, db: DBSession):
    """Disconnect Slack integration."""
    from app.models.external_integration import ExternalIntegration, IntegrationType

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
    return {"ok": True}


@router.post("/integrations/discord/disconnect")
async def mobile_discord_disconnect(user: MobileUser, db: DBSession):
    """Disconnect Discord integration."""
    from app.models.external_integration import ExternalIntegration, IntegrationType

    result = await db.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.user_id == user.id,
            ExternalIntegration.integration_type == IntegrationType.DISCORD,
        )
    )
    integration = result.scalar_one_or_none()
    if integration:
        integration.is_active = False
        integration.access_token = None
        integration.refresh_token = None
        await db.commit()
    return {"ok": True}
