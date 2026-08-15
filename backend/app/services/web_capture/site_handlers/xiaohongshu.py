from __future__ import annotations

from urllib.parse import urlparse

from backend.app.errors import AppError
from backend.app.services.web_capture.site_handlers.generic import GenericWebCaptureHandler


class XiaohongshuWebCaptureHandler:
    hostnames = {
        "xiaohongshu.com",
        "www.xiaohongshu.com",
    }

    def __init__(self, *, fallback: GenericWebCaptureHandler) -> None:
        self._fallback = fallback

    def supports(self, *, url: str, parser_name: str) -> bool:
        if parser_name != self._fallback.parser_name:
            return False
        hostname = (urlparse(url).hostname or "").lower()
        return hostname in self.hostnames

    def capture_url(
        self,
        *,
        url: str,
        parser_name: str,
        session_profile_id: str | None,
        cancel_check=None,
    ) -> dict[str, object]:
        try:
            captured = self._fallback.capture_url(
                url=url,
                parser_name=parser_name,
                session_profile_id=session_profile_id,
                cancel_check=cancel_check,
            )
        except AppError as exc:
            raise self._translate_error(exc) from exc

        warnings = [str(item) for item in (captured.get("warnings") or [])]
        restriction_warning = self._detect_restriction_warning(captured)
        if restriction_warning and restriction_warning not in warnings:
            warnings.append(restriction_warning)
        captured["warnings"] = warnings
        return captured

    @staticmethod
    def _translate_error(exc: AppError) -> AppError:
        if exc.error_category == "AUTH_REQUIRED":
            return AppError(
                status_code=exc.status_code,
                error_category=exc.error_category,
                error_message=(
                    "小红书页面需要可用的登录会话。请先确认会话仍然有效，再重试抓取。"
                ),
            )

        if exc.error_category == "FETCH_FAILED":
            return AppError(
                status_code=exc.status_code,
                error_category="UNSUPPORTED_SITE",
                error_message=(
                    "小红书页面当前不适合走通用抓取链路，可能返回了站点限制页、登录页或动态重定向页。"
                    "建议改为站点专用接入，不要把这类逻辑混进通用抓取。"
                ),
            )

        return exc

    @staticmethod
    def _detect_restriction_warning(captured: dict[str, object]) -> str | None:
        text = " ".join(
            str(captured.get(field) or "")
            for field in ("title", "raw_text", "preview_text", "markdown_text")
        ).lower()

        restriction_markers = (
            "请登录",
            "登录后",
            "验证码",
            "安全验证",
            "异常访问",
            "访问受限",
            "稍后再试",
        )
        if any(marker in text for marker in restriction_markers):
            return "小红书页面疑似返回登录页、验证页或受限页面，当前结果可能不完整。"

        raw_text = str(captured.get("raw_text") or "").strip()
        if len(raw_text) < 80:
            return "小红书页面正文过短，当前结果可能不是目标内容页。"

        return None
