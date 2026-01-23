"""
FixPix Backend Configuration

This module ensures Celery is loaded when Django starts (if available).
"""

# Try to import Celery app (optional - for production with Redis)
try:
    from .celery import app as celery_app
    __all__ = ('celery_app',)
except ImportError:
    # Celery not installed - works fine for development
    celery_app = None
    __all__ = ()
