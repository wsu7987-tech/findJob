from __future__ import annotations

from collections.abc import Callable, Sequence

from backend.app.errors import AppError
from backend.app.services.web_capture.site_handlers.base import WebCaptureHandler
from backend.app.services.web_capture.site_handlers.registry import build_default_capture_handlers


class WebCaptureService:
    def __init__(
        self,
        *,
        handlers: Sequence[WebCaptureHandler] | None = None,
        runner=None,
        session_profile_loader: Callable[[str], dict[str, object] | None] | None = None,
    ) -> None:
        self._session_profile_loader = session_profile_loader or (lambda _session_profile_id: None)
        self._handlers = list(
            handlers
            or build_default_capture_handlers(
                runner=runner,
                session_profile_loader=self._session_profile_loader,
            )
        )

    def capture_url(
        self,
        *,
        url: str,
        parser_name: str,
        session_profile_id: str | None,
        cancel_check=None,
    ) -> dict[str, object]:
        for handler in self._handlers:
            if handler.supports(url=url, parser_name=parser_name):
                return handler.capture_url(
                    url=url,
                    parser_name=parser_name,
                    session_profile_id=session_profile_id,
                    cancel_check=cancel_check,
                )

        raise AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message=f"Unsupported web capture parser: {parser_name}",
        )


def build_default_web_capture_service(
    *,
    session_profile_loader: Callable[[str], dict[str, object] | None] | None = None,
) -> WebCaptureService:
    return WebCaptureService(session_profile_loader=session_profile_loader)
