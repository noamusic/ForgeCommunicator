"""
Site configuration model for dynamic platform settings.

Allows super admins to change branding, colors, and other settings
without redeploying the application.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SiteConfig(Base):
    """Dynamic site configuration stored in database.
    
    Overrides environment variable defaults when set.
    Only platform admins can modify these settings.
    """
    
    __tablename__ = "site_configs"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    # Configuration key (unique)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    
    # Configuration value (JSON for complex types)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Optional JSON value for complex settings
    json_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    
    # Metadata
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(nullable=True)  # User ID
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    
    def __repr__(self) -> str:
        return f"<SiteConfig {self.key}={self.value}>"


# Configuration keys
class ConfigKeys:
    """Known configuration keys."""
    
    # Branding
    BRAND_NAME = "brand_name"
    BRAND_COMPANY = "brand_company"
    BRAND_LOGO_URL = "brand_logo_url"
    BRAND_FAVICON_URL = "brand_favicon_url"
    BRAND_SUPPORT_EMAIL = "brand_support_email"
    
    # Theme Colors
    THEME_PRIMARY_COLOR = "theme_primary_color"
    THEME_SECONDARY_COLOR = "theme_secondary_color"
    THEME_ACCENT_COLOR = "theme_accent_color"
    THEME_DARK_MODE_DEFAULT = "theme_dark_mode_default"
    
    # Theme presets
    THEME_PRESET = "theme_preset"  # 'light', 'dark', 'futuristic', 'custom'
    
    # Custom CSS
    CUSTOM_CSS = "custom_css"
    
    # Account Approval
    REQUIRE_ACCOUNT_APPROVAL = "require_account_approval"  # 'true' or 'false'
    REQUIRE_WORKSPACE_CREATE_APPROVAL = "require_workspace_create_approval"  # 'true' or 'false'


# Theme presets
THEME_PRESETS = {
    "light": {
        "primary_color": "#4f46e5",  # Indigo
        "secondary_color": "#1e3a5f",  # Navy
        "accent_color": "#f97316",  # Orange
        "dark_mode_default": False,
        "description": "Classic light theme with indigo accents",
    },
    "dark": {
        "primary_color": "#6366f1",  # Brighter indigo
        "secondary_color": "#1e293b",  # Slate
        "accent_color": "#f59e0b",  # Amber
        "dark_mode_default": True,
        "description": "Modern dark theme",
    },
    "futuristic": {
        "primary_color": "#3b82f6",  # Blue
        "secondary_color": "#0f172a",  # Dark navy
        "accent_color": "#a855f7",  # Purple
        "dark_mode_default": True,
        "description": "Futuristic dark theme with blue/purple accents",
    },
    "buildly": {
        "primary_color": "#6366f1",  # Indigo
        "secondary_color": "#7c3aed",  # Violet
        "accent_color": "#06b6d4",  # Cyan
        "dark_mode_default": True,
        "description": "Buildly brand colors",
    },
}
