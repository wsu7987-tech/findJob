from __future__ import annotations

from backend.app.errors import AppError
from backend.app.services.web_capture.site_handlers.xiaohongshu import XiaohongshuWebCaptureHandler


class _FakeFallback:
    parser_name = "playwright_dom"

    def __init__(self, result=None, error: AppError | None = None) -> None:
        self._result = result
        self._error = error

    def capture_url(self, **kwargs):
        if self._error is not None:
            raise self._error
        return dict(self._result or {})


def test_xiaohongshu_handler_translates_fetch_failure_into_site_specific_message() -> None:
    handler = XiaohongshuWebCaptureHandler(
        fallback=_FakeFallback(
            error=AppError(
                status_code=502,
                error_category="FETCH_FAILED",
                error_message="Failed to render page: some runtime error",
            )
        )
    )

    try:
        handler.capture_url(
            url="https://www.xiaohongshu.com/explore/demo",
            parser_name="playwright_dom",
            session_profile_id=None,
        )
    except AppError as exc:
        assert exc.error_category == "UNSUPPORTED_SITE"
        assert "小红书" in exc.error_message
        assert "通用抓取" in exc.error_message
    else:
        raise AssertionError("Expected AppError to be raised")


def test_xiaohongshu_handler_translates_auth_required_into_clear_session_hint() -> None:
    handler = XiaohongshuWebCaptureHandler(
        fallback=_FakeFallback(
            error=AppError(
                status_code=400,
                error_category="AUTH_REQUIRED",
                error_message="Web session profile is not available.",
            )
        )
    )

    try:
        handler.capture_url(
            url="https://www.xiaohongshu.com/explore/demo",
            parser_name="playwright_dom",
            session_profile_id="session-1",
        )
    except AppError as exc:
        assert exc.error_category == "AUTH_REQUIRED"
        assert "登录会话" in exc.error_message
        assert "小红书" in exc.error_message
    else:
        raise AssertionError("Expected AppError to be raised")


def test_xiaohongshu_handler_warns_when_capture_result_looks_restricted() -> None:
    handler = XiaohongshuWebCaptureHandler(
        fallback=_FakeFallback(
            result={
                "title": "小红书 - 你的生活指南",
                "source_name": "www.xiaohongshu.com",
                "auth_mode": "browser_profile",
                "raw_text": "请登录后继续",
                "markdown_text": "请登录后继续",
                "preview_text": "请登录后继续",
                "preview_pages": [{"page_number": 1, "content_type": "text", "content": "请登录后继续"}],
                "warnings": [],
            }
        )
    )

    captured = handler.capture_url(
        url="https://www.xiaohongshu.com/explore/demo",
        parser_name="playwright_dom",
        session_profile_id=None,
    )

    assert captured["warnings"]
    assert "登录" in captured["warnings"][0]
