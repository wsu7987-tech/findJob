from backend.app.services.web_capture.site_handlers.base import WebCaptureHandler
from backend.app.services.web_capture.site_handlers.generic import GenericWebCaptureHandler
from backend.app.services.web_capture.site_handlers.xiaohongshu import XiaohongshuWebCaptureHandler

__all__ = [
    "GenericWebCaptureHandler",
    "WebCaptureHandler",
    "XiaohongshuWebCaptureHandler",
]
