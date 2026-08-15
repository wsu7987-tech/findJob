from __future__ import annotations

from collections.abc import Callable

from backend.app.services.web_capture.playwright_runner import PlaywrightRunner
from backend.app.services.web_capture.site_handlers.generic import GenericWebCaptureHandler
from backend.app.services.web_capture.site_handlers.xiaohongshu import XiaohongshuWebCaptureHandler


def build_default_capture_handlers(
    *,
    runner: PlaywrightRunner | None = None,
    session_profile_loader: Callable[[str], dict[str, object] | None] | None = None,
):
    generic_handler = GenericWebCaptureHandler(
        runner=runner,
        session_profile_loader=session_profile_loader,
    )
    return [
        XiaohongshuWebCaptureHandler(fallback=generic_handler),
        generic_handler,
    ]
