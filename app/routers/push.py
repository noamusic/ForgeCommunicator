"""
Push notifications router for web push subscriptions.
"""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

from app.deps import CurrentUser, DBSession
from app.models.push_subscription import PushSubscription
from app.settings import settings

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/vapid-public-key")
async def get_vapid_public_key():
    """Get the VAPID public key for push subscription."""
    if not settings.vapid_public_key:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Push notifications not configured"
        )
    return JSONResponse({"publicKey": settings.vapid_public_key})


@router.get("/status")
async def get_push_status(
    user: CurrentUser,
    db: DBSession,
):
    """Get push notification status for the current user."""
    from sqlalchemy import func
    
    # Count user's subscriptions
    result = await db.execute(
        select(func.count()).select_from(PushSubscription).where(
            PushSubscription.user_id == user.id
        )
    )
    subscriptions_count = result.scalar() or 0
    
    return JSONResponse({
        "vapid_configured": bool(settings.vapid_public_key),
        "subscriptions_count": subscriptions_count,
    })


@router.post("/subscribe")
async def subscribe(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    endpoint: Annotated[str, Form()],
    p256dh: Annotated[str, Form()],
    auth: Annotated[str, Form()],
):
    """Subscribe to push notifications."""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info("Push subscription request from user %s", user.id)
    
    if not settings.vapid_public_key:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Push notifications not configured"
        )
    
    # Check if subscription already exists
    result = await db.execute(
        select(PushSubscription).where(
            PushSubscription.user_id == user.id,
            PushSubscription.endpoint == endpoint,
        )
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        # Update keys in case they changed
        existing.p256dh_key = p256dh
        existing.auth_key = auth
        existing.user_agent = request.headers.get("User-Agent")
        logger.info("Updated existing push subscription for user %s", user.id)
    else:
        # Create new subscription
        subscription = PushSubscription(
            user_id=user.id,
            endpoint=endpoint,
            p256dh_key=p256dh,
            auth_key=auth,
            user_agent=request.headers.get("User-Agent"),
        )
        db.add(subscription)
        logger.info("Created new push subscription for user %s", user.id)
    
    await db.commit()
    
    return JSONResponse({"status": "subscribed"})


@router.post("/unsubscribe")
async def unsubscribe(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    endpoint: Annotated[str, Form()],
):
    """Unsubscribe from push notifications."""
    result = await db.execute(
        select(PushSubscription).where(
            PushSubscription.user_id == user.id,
            PushSubscription.endpoint == endpoint,
        )
    )
    subscription = result.scalar_one_or_none()
    
    if subscription:
        await db.delete(subscription)
        await db.commit()
    
    return JSONResponse({"status": "unsubscribed"})


@router.post("/test")
async def send_test_notification(
    user: CurrentUser,
    db: DBSession,
):
    """Send a test push notification to the current user."""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info("Test notification requested by user %s", user.id)
    
    try:
        if not settings.vapid_public_key:
            logger.warning("Test notification failed - VAPID not configured")
            return JSONResponse(
                {"status": "error", "message": "Push notifications not configured"},
                status_code=status.HTTP_501_NOT_IMPLEMENTED
            )
        
        from app.services.push import push_service
        
        sent = await push_service.send_notification(
            db=db,
            user_id=user.id,
            title="Test Notification",
            body="Push notifications are working! 🎉",
            url="/profile",
            tag="test-notification",
        )
        
        logger.info("Test notification result for user %s: sent=%d", user.id, sent)
        
        if sent > 0:
            return JSONResponse({"status": "sent", "count": sent})
        else:
            return JSONResponse(
                {"status": "no_subscriptions", "message": "No active push subscriptions found"},
                status_code=status.HTTP_404_NOT_FOUND
            )
    except Exception as e:
        logger.error("Test notification error for user %s: %s", user.id, str(e))
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.get("/status")
async def get_push_status(
    user: CurrentUser,
    db: DBSession,
):
    """Get push notification status for debugging."""
    # Check if VAPID is configured
    vapid_configured = bool(settings.vapid_public_key and settings.vapid_private_key)
    
    # Get user's subscriptions
    result = await db.execute(
        select(PushSubscription).where(PushSubscription.user_id == user.id)
    )
    subscriptions = result.scalars().all()
    
    sub_info = []
    for sub in subscriptions:
        # Extract endpoint domain for debugging
        from urllib.parse import urlparse
        endpoint_domain = urlparse(sub.endpoint).netloc if sub.endpoint else "unknown"
        sub_info.append({
            "id": sub.id,
            "endpoint_domain": endpoint_domain,
            "user_agent": sub.user_agent[:50] if sub.user_agent else None,
            "created_at": sub.created_at.isoformat() if hasattr(sub, 'created_at') and sub.created_at else None,
        })
    
    return JSONResponse({
        "vapid_configured": vapid_configured,
        "subscription_count": len(subscriptions),
        "subscriptions": sub_info,
    })


@router.post("/clear-all")
async def clear_all_subscriptions(
    user: CurrentUser,
    db: DBSession,
):
    """Clear all push subscriptions for the current user.
    
    Use this when VAPID keys have changed and old subscriptions are invalid.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    result = await db.execute(
        select(PushSubscription).where(PushSubscription.user_id == user.id)
    )
    subscriptions = result.scalars().all()
    
    count = len(subscriptions)
    for sub in subscriptions:
        await db.delete(sub)
    
    await db.commit()
    
    logger.info("Cleared %d push subscriptions for user %s", count, user.id)
    
    return JSONResponse({
        "status": "cleared",
        "count": count,
        "message": f"Cleared {count} subscription(s). Please re-enable notifications."
    })
