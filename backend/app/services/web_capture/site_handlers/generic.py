from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse

from backend.app.errors import AppError
from backend.app.services.web_capture.auth_sessions import resolve_capture_session
from backend.app.services.web_capture.content_extractor import extract_rendered_document
from backend.app.services.web_capture.image_ocr import extract_ocr_segments, filter_ocr_candidates
from backend.app.services.web_capture.playwright_runner import PlaywrightRunner


class GenericWebCaptureHandler:
    parser_name = "playwright_dom"

    def __init__(
        self,
        *,
        runner: PlaywrightRunner | None = None,
        session_profile_loader: Callable[[str], dict[str, object] | None] | None = None,
    ) -> None:
        self._runner = runner or PlaywrightRunner()
        self._session_profile_loader = session_profile_loader or (lambda _session_profile_id: None)

    def supports(self, *, url: str, parser_name: str) -> bool:
        del url
        return parser_name == self.parser_name

    def capture_url(
        self,
        *,
        url: str,
        parser_name: str,
        session_profile_id: str | None,
        cancel_check=None,
    ) -> dict[str, object]:
        preferred_profile = None
        if session_profile_id:
            preferred_profile = self._session_profile_loader(session_profile_id)
            if preferred_profile is None:
                raise AppError(
                    status_code=400,
                    error_category="AUTH_REQUIRED",
                    error_message="Web session profile is not available.",
                )

        session = resolve_capture_session(preferred_profile=preferred_profile)
        rendered = self._runner.render_page(
            url=url,
            session=session,
            parser_name=parser_name,
            cancel_check=cancel_check,
        )

        image_elements = rendered.get("image_elements") or []
        warnings: list[str] = []
        ocr_segments = list(rendered.get("ocr_segments") or [])
        if image_elements:
            filtered_candidates = filter_ocr_candidates(image_elements)
            try:
                ocr_segments.extend(extract_ocr_segments(filtered_candidates))
            except AppError as exc:
                warnings.append(exc.error_message)

        extracted = extract_rendered_document(
            url=url,
            title=str(rendered.get("title") or ""),
            rendered_html=str(rendered.get("rendered_html") or ""),
            ocr_segments=ocr_segments,
        )
        return {
            "title": extracted.title,
            "source_name": urlparse(url).netloc or url,
            "auth_mode": session.mode,
            "raw_text": extracted.raw_text,
            "markdown_text": extracted.markdown_text,
            "preview_text": extracted.preview_text,
            "preview_pages": [
                {
                    "page_number": item.page_number,
                    "content_type": item.content_type,
                    "content": item.content,
                }
                for item in extracted.preview_pages
            ],
            "warnings": warnings,
        }
