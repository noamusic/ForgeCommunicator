"""
FastAPI application entry point.
"""

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.db import close_db, init_db
from app.settings import settings
from app.github_error_reporter import CombinedErrorReporter

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s" if settings.log_format == "text" else None,
)
logger = logging.getLogger(__name__)

# Initialize error reporter for GitHub Issues and Labs Punchlist
error_reporter = CombinedErrorReporter(
    github_repo=settings.github_error_repo,
    github_token=settings.github_error_token,
    github_max_comments=settings.github_error_max_comments,
    labs_api_url=settings.labs_api_url,
    labs_api_token=settings.labs_api_key,
    labs_product_uuid=settings.labs_error_product_uuid,
) if settings.github_error_reporting_enabled or settings.labs_error_reporting_enabled else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    logger.info("Starting Forge Communicator...")
    await init_db()
    yield
    logger.info("Shutting down Forge Communicator...")
    await close_db()


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request ID middleware
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Add request ID to all requests for logging."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# Session refresh middleware - keeps cookies in sync with sliding sessions
@app.middleware("http")
async def refresh_session_cookie(request: Request, call_next):
    """Refresh session cookie on authenticated requests to implement sliding sessions.
    
    PWA Note: Uses longer expiration (30 days for PWA vs 7 days for browser) to handle
    iOS Safari's aggressive cookie clearing in standalone mode.
    """
    response = await call_next(request)
    
    # Check if session was refreshed (marked by the dependency)
    if hasattr(request.state, 'session_refreshed') and request.state.session_refreshed:
        session_token = request.cookies.get('session_token')
        if session_token:
            # Detect PWA mode from Sec-Fetch-Dest header or display-mode
            is_pwa = request.headers.get('Sec-Fetch-Dest') == 'document' and \
                     request.headers.get('Sec-Fetch-Mode') == 'navigate' and \
                     'standalone' in request.headers.get('Sec-Fetch-Site', '')
            
            # Use longer expiration for PWA to prevent frequent logouts on iOS
            # iOS Safari in PWA mode can be aggressive about clearing cookies
            max_age = settings.session_expire_hours * 3600
            if is_pwa or request.headers.get('X-PWA-Mode') == 'standalone':
                max_age = max(max_age, 30 * 24 * 3600)  # At least 30 days for PWA
            
            response.set_cookie(
                key="session_token",
                value=session_token,
                httponly=True,
                secure=not settings.debug,
                samesite="lax",
                max_age=max_age,
                path="/",  # Explicit path ensures cookie works across all routes
            )
    
    return response


# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Use shared templates with brand context
from app.templates_config import templates


# Health check endpoint
@app.get("/healthz", tags=["health"])
@app.get("/health", tags=["health"])
async def healthz():
    """Health check endpoint for load balancers."""
    return {"status": "healthy"}


# Version endpoint for cache management
@app.get("/version", tags=["meta"])
async def version():
    """Return app version for service worker cache invalidation.
    
    The service worker checks this to detect when a new version is deployed.
    Combined with build_sha, this allows automatic cache busting on deploy.
    """
    return {
        "version": settings.app_version,
        "build_sha": settings.build_sha,
        "cache_key": f"forge-communicator-{settings.app_version}-{settings.build_sha[:8] if settings.build_sha else 'dev'}",
    }


# Service Worker - must be served from root for correct scope
@app.get("/sw.js", tags=["pwa"])
async def service_worker():
    """Serve service worker from root for full scope coverage.
    
    Service workers can only control pages at their level or below.
    By serving from root, the SW can control the entire app.
    """
    from fastapi.responses import FileResponse
    import os
    sw_path = os.path.join(os.path.dirname(__file__), "static", "sw.js")
    return FileResponse(
        sw_path,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/",
        }
    )


# PWA Manifest - dynamic with branding
@app.get("/manifest.json", tags=["pwa"])
async def pwa_manifest():
    """Serve PWA manifest with dynamic branding."""
    from fastapi.responses import JSONResponse
    from app.brand import get_brand
    
    brand = get_brand()
    
    manifest = {
        "name": brand.full_name,  # e.g., "Buildly Communicator"
        "short_name": brand.name,  # e.g., "Communicator"
        "description": f"{brand.company} team communication platform",
        "id": "/workspaces",
        "start_url": "/workspaces",
        "scope": "/",
        "display": "standalone",
        "display_override": ["standalone", "minimal-ui"],
        "background_color": "#0f172a",
        "theme_color": brand.primary_color or "#3b82f6",
        "orientation": "any",
        "icons": [
            {"src": "/static/icons/icon-180x180.png", "sizes": "180x180", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-72x72.png", "sizes": "72x72", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-96x96.png", "sizes": "96x96", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-128x128.png", "sizes": "128x128", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-144x144.png", "sizes": "144x144", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-152x152.png", "sizes": "152x152", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-192x192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-384x384.png", "sizes": "384x384", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-512x512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
        "screenshots": [
            {"src": "/static/screenshots/mobile.png", "sizes": "390x844", "type": "image/png", "form_factor": "narrow"},
            {"src": "/static/screenshots/desktop.png", "sizes": "1920x1080", "type": "image/png", "form_factor": "wide"},
        ],
        "categories": ["business", "productivity", "social"],
        "prefer_related_applications": False,
        "shortcuts": [
            {"name": "Workspaces", "short_name": "Workspaces", "url": "/workspaces", "icons": [{"src": "/static/icons/icon-96x96.png", "sizes": "96x96"}]},
            {"name": "Profile", "short_name": "Profile", "url": "/profile", "icons": [{"src": "/static/icons/icon-96x96.png", "sizes": "96x96"}]},
        ],
    }
    
    return JSONResponse(
        content=manifest,
        headers={"Cache-Control": "public, max-age=3600"},
    )


# Offline page for PWA
@app.get("/offline", response_class=HTMLResponse)
async def offline_page(request: Request):
    """Offline page for PWA."""
    return templates.TemplateResponse(
        "offline.html",
        {"request": request},
    )


# Meta endpoint for Forge Marketplace diagnostics
@app.get("/meta", tags=["meta"])
async def meta():
    """Return application metadata for marketplace diagnostics."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "build_sha": settings.build_sha,
    }


# Root redirect
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Redirect to workspaces or login."""
    from app.deps import get_current_user_optional, get_db
    from starlette.responses import RedirectResponse
    
    async for db in get_db():
        user = await get_current_user_optional(
            request=request,
            db=db,
            session_token=request.cookies.get("session_token"),
        )
        if user:
            return RedirectResponse("/workspaces", status_code=302)
        return RedirectResponse("/auth/login", status_code=302)


# Import and include routers
from app.routers import admin, api, artifacts, auth, channels, invites, messages, notes, profile, push, reactions, realtime, sync, workspaces

app.include_router(auth.router)
app.include_router(workspaces.router)
app.include_router(channels.router)
app.include_router(channels.dm_router)  # DM JSON API
app.include_router(messages.router)
app.include_router(artifacts.router)
app.include_router(reactions.router)
app.include_router(realtime.router)
app.include_router(profile.router)
app.include_router(push.router)
app.include_router(sync.router)
app.include_router(admin.router)
app.include_router(invites.router)
app.include_router(notes.router)
app.include_router(api.router)  # DRF-compatible API for CollabHub

# Import and include integrations router
from app.routers import integrations
app.include_router(integrations.router)

# Import and include AI router
from app.routers import ai
app.include_router(ai.router)


# Error handlers - Handle HTTPException from FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import HTTPException


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle all HTTP exceptions with proper responses for PWA, HTMX, and API."""
    status_code = exc.status_code
    detail = exc.detail if hasattr(exc, 'detail') else str(exc)
    
    # For 401 Unauthorized - redirect to login
    if status_code == 401:
        # For HTMX requests, return redirect header
        if request.headers.get("HX-Request"):
            response = HTMLResponse("", status_code=200)
            response.headers["HX-Redirect"] = "/auth/login"
            return response
        
        # For API requests expecting JSON, return JSON error
        accept = request.headers.get("Accept", "")
        if "application/json" in accept and "text/html" not in accept:
            return JSONResponse(
                {"detail": detail or "Not authenticated"},
                status_code=401,
            )
        
        # For browser/PWA requests, redirect to login
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/auth/login", status_code=302)
    
    # For 403 Forbidden
    if status_code == 403:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<div class="text-red-500 p-4">Access denied</div>',
                status_code=403,
            )
        
        accept = request.headers.get("Accept", "")
        if "application/json" in accept and "text/html" not in accept:
            return JSONResponse({"detail": detail or "Access denied"}, status_code=403)
        
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": detail, "status_code": 403, "brand": getattr(request.state, 'brand', None)},
            status_code=403,
        )
    
    # For 404 Not Found
    if status_code == 404:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<div class="text-red-500 p-4">Page not found</div>',
                status_code=404,
            )
        
        accept = request.headers.get("Accept", "")
        if "application/json" in accept and "text/html" not in accept:
            return JSONResponse({"detail": detail or "Not found"}, status_code=404)
        
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": detail, "status_code": 404, "brand": getattr(request.state, 'brand', None)},
            status_code=404,
        )
    
    # For 5xx Server Errors
    if status_code >= 500:
        import traceback
        from datetime import datetime, timezone
        
        logger.error(f"Server error {status_code}: {detail}")
        
        # Report 5xx errors to GitHub/Labs
        if error_reporter:
            try:
                user = getattr(request.state, 'user', None)
                user_display = None
                if user and hasattr(user, 'email'):
                    user_display = user.email
                elif user and hasattr(user, 'id'):
                    user_display = f"User ID: {user.id}"
                
                error_context = {
                    'error_type': f'HTTPException_{status_code}',
                    'error_message': str(detail),
                    'path': str(request.url.path),
                    'method': request.method,
                    'user': user_display,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                }
                traceback_text = f"HTTP {status_code} Error\nDetail: {detail}\nPath: {request.url.path}"
                
                import asyncio
                loop = asyncio.get_event_loop()
                loop.run_in_executor(
                    None,
                    lambda: error_reporter.report_error(error_context, traceback_text)
                )
            except Exception as report_exc:
                logger.warning(f"Failed to report error to GitHub/Labs: {report_exc}")
        
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<div class="text-red-500 p-4">Server error. Please try again.</div>',
                status_code=status_code,
            )
        
        accept = request.headers.get("Accept", "")
        if "application/json" in accept and "text/html" not in accept:
            return JSONResponse({"detail": "Server error"}, status_code=status_code)
        
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": "Server error", "status_code": status_code, "brand": getattr(request.state, 'brand', None)},
            status_code=status_code,
        )
    
    # For other HTTP errors, return appropriate response
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            f'<div class="text-red-500 p-4">{detail}</div>',
            status_code=status_code,
        )
    
    accept = request.headers.get("Accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return JSONResponse({"detail": detail}, status_code=status_code)
    
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "error": detail, "status_code": status_code, "brand": getattr(request.state, 'brand', None)},
        status_code=status_code,
    )


# Generic exception handler for unhandled errors
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Handle unhandled exceptions - show error page instead of white screen."""
    import traceback
    from datetime import datetime, timezone
    
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    
    # Report to GitHub Issues and Labs Punchlist
    if error_reporter:
        try:
            # Get user info if available
            user = getattr(request.state, 'user', None)
            user_display = None
            if user and hasattr(user, 'email'):
                user_display = user.email
            elif user and hasattr(user, 'id'):
                user_display = f"User ID: {user.id}"
            
            error_context = {
                'error_type': type(exc).__name__,
                'error_message': str(exc),
                'path': str(request.url.path),
                'method': request.method,
                'user': user_display,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }
            traceback_text = traceback.format_exc()
            
            # Report async-safe (fire and forget in background)
            import asyncio
            loop = asyncio.get_event_loop()
            loop.run_in_executor(
                None,
                lambda: error_reporter.report_error(error_context, traceback_text)
            )
        except Exception as report_exc:
            logger.warning(f"Failed to report error to GitHub/Labs: {report_exc}")
    
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            '<div class="text-red-500 p-4">An unexpected error occurred. Please try again.</div>',
            status_code=500,
        )
    
    accept = request.headers.get("Accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
    
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "error": "An unexpected error occurred", "status_code": 500, "brand": getattr(request.state, 'brand', None)},
        status_code=500,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
