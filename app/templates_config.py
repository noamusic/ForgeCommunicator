"""
Shared Jinja2 templates configuration.

All routers should import templates from here to ensure brand context is available.
"""

import re
from datetime import datetime, timedelta, timezone
from markupsafe import Markup
from fastapi.templating import Jinja2Templates
import markdown

from app.brand import get_brand
from app.settings import settings


def markdown_filter(text: str) -> Markup:
    """Convert markdown text to HTML, safe for display."""
    if not text:
        return Markup("")
    
    # Convert markdown to HTML
    # Use safe extensions only
    html = markdown.markdown(
        text,
        extensions=['nl2br', 'fenced_code', 'tables'],
        output_format='html'
    )
    
    return Markup(html)


def simple_markdown_filter(text: str) -> Markup:
    """
    Simple markdown conversion for inline formatting only.
    Handles: **bold**, *italic*, `code`, ~~strikethrough~~
    Also preserves HTML content (from Labs API imports) while sanitizing it.
    """
    if not text:
        return Markup("")
    
    import html as html_module
    
    # Check if text contains HTML tags (from Labs API)
    if re.search(r'<[a-zA-Z][^>]*>', text):
        # Try to use bleach for HTML sanitization if available
        try:
            import bleach
            # Sanitize HTML to allow only safe tags
            allowed_tags = [
                'p', 'br', 'strong', 'b', 'em', 'i', 'u', 's', 'del',
                'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                'ul', 'ol', 'li',
                'a', 'code', 'pre', 'blockquote',
                'table', 'thead', 'tbody', 'tr', 'th', 'td',
                'span', 'div',
            ]
            allowed_attrs = {
                'a': ['href', 'title', 'target', 'rel'],
                'span': ['class'],
                'div': ['class'],
                'code': ['class'],
                'pre': ['class'],
            }
            # Clean HTML and return
            clean_html = bleach.clean(
                text,
                tags=allowed_tags,
                attributes=allowed_attrs,
                strip=True,
            )
            return Markup(clean_html)
        except ImportError:
            # If bleach not installed, strip all HTML tags for safety
            clean_text = re.sub(r'<[^>]+>', '', text)
            return Markup(html_module.escape(clean_text).replace('\n', '<br>'))
    
    # Escape HTML for plain text input
    text = html_module.escape(text)
    
    # Convert markdown patterns
    # Bold: **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
    
    # Italic: *text* or _text_ (but not inside words)
    text = re.sub(r'(?<!\w)\*([^*]+?)\*(?!\w)', r'<em>\1</em>', text)
    text = re.sub(r'(?<!\w)_([^_]+?)_(?!\w)', r'<em>\1</em>', text)
    
    # Inline code: `code`
    text = re.sub(r'`([^`]+?)`', r'<code class="bg-gray-100 dark:bg-gray-700 px-1 py-0.5 rounded text-sm font-mono">\1</code>', text)
    
    # Strikethrough: ~~text~~
    text = re.sub(r'~~(.+?)~~', r'<del>\1</del>', text)
    
    # Links: [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" class="text-indigo-600 hover:underline" target="_blank" rel="noopener">\1</a>', text)
    
    # Preserve newlines
    text = text.replace('\n', '<br>')
    
    return Markup(text)


# Create shared templates instance
templates = Jinja2Templates(directory="app/templates")

# Add brand to all template contexts globally
templates.env.globals["brand"] = get_brand()

# Add settings for feature flags and configuration
templates.env.globals["settings"] = settings

# Expose datetime helpers for date separators and relative timestamps
templates.env.globals["now"] = lambda: datetime.now(timezone.utc)
templates.env.globals["timedelta"] = timedelta

# Add markdown filters
templates.env.filters["markdown"] = markdown_filter
templates.env.filters["md"] = simple_markdown_filter
