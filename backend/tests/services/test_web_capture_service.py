from __future__ import annotations


class _FakeRunner:
    def __init__(self, rendered: dict[str, object]) -> None:
        self.rendered = rendered

    def render_page(self, **kwargs):
        return self.rendered


def test_capture_url_merges_ocr_segments_into_extracted_markdown(monkeypatch) -> None:
    from backend.app.services.web_capture.service import WebCaptureService

    rendered = {
        "title": "Chart Page",
        "rendered_html": """
<!doctype html>
<html>
  <body>
    <article>
      <h1>Chart Page</h1>
      <p>Revenue chart</p>
    </article>
  </body>
</html>
""",
        "image_elements": [
            {
                "width": 400,
                "height": 240,
                "decorative": False,
                "anchor_text": "Revenue chart",
                "screenshot_base64": "stub-image",
            }
        ],
    }

    monkeypatch.setattr(
        "backend.app.services.web_capture.site_handlers.generic.extract_ocr_segments",
        lambda elements: [{"anchor_text": "Revenue chart", "text": "Q1 42%\nQ2 57%"}],
    )

    service = WebCaptureService(runner=_FakeRunner(rendered))
    captured = service.capture_url(
        url="https://example.com/chart",
        parser_name="playwright_dom",
        session_profile_id=None,
    )

    assert "Q1 42%" in str(captured["markdown_text"])
    assert "Q2 57%" in str(captured["raw_text"])
    assert captured["warnings"] == []


def test_capture_url_uses_first_matching_site_handler() -> None:
    from backend.app.services.web_capture.service import WebCaptureService

    calls: list[str] = []

    class _SiteHandler:
        def supports(self, *, url: str, parser_name: str) -> bool:
            return "xiaohongshu.com" in url and parser_name == "playwright_dom"

        def capture_url(self, **kwargs):
            calls.append("site")
            return {
                "title": "XHS",
                "source_name": "xiaohongshu.com",
                "auth_mode": "browser_profile",
                "raw_text": "site result",
                "markdown_text": "# Site",
                "preview_text": "# Site",
                "preview_pages": [{"page_number": 1, "content_type": "markdown", "content": "# Site"}],
                "warnings": [],
            }

    class _GenericHandler:
        def supports(self, *, url: str, parser_name: str) -> bool:
            del url, parser_name
            return True

        def capture_url(self, **kwargs):
            calls.append("generic")
            return {
                "title": "Generic",
                "source_name": "example.com",
                "auth_mode": "none",
                "raw_text": "generic result",
                "markdown_text": "# Generic",
                "preview_text": "# Generic",
                "preview_pages": [{"page_number": 1, "content_type": "markdown", "content": "# Generic"}],
                "warnings": [],
            }

    service = WebCaptureService(handlers=[_SiteHandler(), _GenericHandler()])

    captured = service.capture_url(
        url="https://www.xiaohongshu.com/explore/demo",
        parser_name="playwright_dom",
        session_profile_id=None,
    )

    assert captured["raw_text"] == "site result"
    assert calls == ["site"]
