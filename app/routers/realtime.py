"""
WebSocket realtime router for live message updates.
"""

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from sqlalchemy.orm import selectinload

from app.db import async_session_maker
from app.models.channel import Channel
from app.models.membership import ChannelMembership, Membership
from app.models.user import User
from app.models.user_session import UserSession

logger = logging.getLogger(__name__)
router = APIRouter(tags=["realtime"])


# Connection manager for WebSocket connections
class ConnectionManager:
    """Manage WebSocket connections per channel.

    Multiple tabs / devices for the same user are supported — each WebSocket
    gets its own slot keyed by the websocket object itself so opening a second
    tab never silently drops the first connection.
    """

    def __init__(self):
        # channel_id -> list of (user_id, WebSocket) tuples
        self.active_connections: dict[int, list[tuple[int, WebSocket]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, channel_id: int, user_id: int) -> None:
        """Accept and register a WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections[channel_id].append((user_id, websocket))
        logger.info(f"WebSocket connected to channel {channel_id} for user {user_id} "
                    f"(total: {len(self.active_connections[channel_id])})")

    async def disconnect(self, websocket: WebSocket, channel_id: int, user_id: int) -> None:
        """Remove a specific WebSocket connection."""
        async with self._lock:
            if channel_id in self.active_connections:
                self.active_connections[channel_id] = [
                    (uid, ws)
                    for uid, ws in self.active_connections[channel_id]
                    if ws is not websocket
                ]
                if not self.active_connections[channel_id]:
                    del self.active_connections[channel_id]
        logger.info(f"WebSocket disconnected from channel {channel_id} for user {user_id}")

    async def broadcast_to_channel(
        self,
        channel_id: int,
        message: dict[str, Any],
        exclude_user_id: int | None = None,
    ) -> None:
        """Broadcast a message to all connections in a channel, optionally excluding sender.

        When exclude_user_id is set, ALL tabs/connections for that user are skipped —
        the sender's own HTMX response already rendered the message in the posting tab,
        and other tabs belonging to the same user should also receive the broadcast so
        they stay in sync.  We therefore skip only the exact websocket that sent the
        message (identified by user_id on the HTTP side), which means we do NOT exclude
        other tabs of the same user.  The JS deduplication guard
        (``document.getElementById('message-' + id)``) handles the rare race where the
        HTMX response and the WS broadcast both arrive before the DOM is updated.
        """
        async with self._lock:
            snapshot = list(self.active_connections.get(channel_id, []))

        stale: list[tuple[int, WebSocket]] = []
        for uid, ws in snapshot:
            if exclude_user_id is not None and uid == exclude_user_id:
                # Skip ALL connections belonging to the sender so they don't see
                # their own message duplicated (sender already has it via HTMX).
                continue
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket for user {uid} in channel {channel_id}: {e}")
                stale.append((uid, ws))

        for uid, ws in stale:
            await self.disconnect(ws, channel_id, uid)

    def get_connection_count(self, channel_id: int) -> int:
        """Get number of active connections for a channel."""
        return len(self.active_connections.get(channel_id, []))


# Global connection manager
manager = ConnectionManager()


async def verify_websocket_access(
    websocket: WebSocket,
    workspace_id: int,
    channel_id: int,
) -> User | None:
    """Verify WebSocket connection has access to channel."""
    session_token = websocket.cookies.get("session_token")
    if not session_token:
        return None
    
    async with async_session_maker() as db:
        # Get user from UserSession table (multi-device support)
        result = await db.execute(
            select(UserSession)
            .where(UserSession.session_token == session_token)
            .options(selectinload(UserSession.user))
        )
        session = result.scalar_one_or_none()
        
        if session and session.is_valid() and session.user:
            user = session.user
        else:
            # Fallback: Check old single-session token on user table (for migration)
            result = await db.execute(
                select(User).where(User.session_token == session_token)
            )
            user = result.scalar_one_or_none()
            if not user or not user.is_session_valid():
                return None
        
        # Check workspace membership
        result = await db.execute(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user.id,
            )
        )
        if not result.scalar_one_or_none():
            return None
        
        # Check channel access
        result = await db.execute(
            select(Channel).where(
                Channel.id == channel_id,
                Channel.workspace_id == workspace_id,
            )
        )
        channel = result.scalar_one_or_none()
        if not channel:
            return None
        
        # Check private channel access
        if channel.is_private:
            result = await db.execute(
                select(ChannelMembership).where(
                    ChannelMembership.channel_id == channel_id,
                    ChannelMembership.user_id == user.id,
                )
            )
            if not result.scalar_one_or_none():
                return None
        
        return user


@router.websocket("/ws/workspaces/{workspace_id}/channels/{channel_id}")
async def websocket_channel(
    websocket: WebSocket,
    workspace_id: int,
    channel_id: int,
):
    """WebSocket endpoint for real-time channel updates."""
    # Verify access
    user = await verify_websocket_access(websocket, workspace_id, channel_id)
    if not user:
        await websocket.close(code=4003)
        return
    
    # Connect with user_id for tracking
    await manager.connect(websocket, channel_id, user.id)
    
    try:
        # Send connection confirmation
        await websocket.send_json({
            "type": "connected",
            "channel_id": channel_id,
            "user_id": user.id,
        })
        
        # Keep connection alive and handle incoming messages
        while True:
            try:
                data = await websocket.receive_json()
                
                # Handle ping/pong for keepalive
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                
                # Handle typing indicator
                elif data.get("type") == "typing":
                    await manager.broadcast_to_channel(channel_id, {
                        "type": "typing",
                        "user_id": user.id,
                        "user_name": user.display_name,
                    }, exclude_user_id=user.id)
                
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                continue
    
    finally:
        await manager.disconnect(websocket, channel_id, user.id)


async def broadcast_new_message(
    channel_id: int,
    message_html: str,
    message_id: int,
    user_id: int,
    user_name: str,
    parent_id: int | None = None,
) -> None:
    """Broadcast a new message to channel subscribers (excludes sender)."""
    await manager.broadcast_to_channel(channel_id, {
        "type": "new_message",
        "message_id": message_id,
        "parent_id": parent_id,
        "user_id": user_id,
        "user_name": user_name,
        "html": message_html,
    }, exclude_user_id=user_id)  # Exclude sender - they already got the message from HTMX response


async def broadcast_message_update(
    channel_id: int,
    message_id: int,
    message_html: str,
) -> None:
    """Broadcast a message update to channel subscribers."""
    await manager.broadcast_to_channel(channel_id, {
        "type": "message_updated",
        "message_id": message_id,
        "html": message_html,
    })


async def broadcast_message_delete(
    channel_id: int,
    message_id: int,
) -> None:
    """Broadcast a message deletion to channel subscribers."""
    await manager.broadcast_to_channel(channel_id, {
        "type": "message_deleted",
        "message_id": message_id,
    })
