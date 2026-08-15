from __future__ import annotations

from backend.app.errors import AppError
from backend.app.services.web_capture.playwright_runner import PlaywrightRunner
from backend.app.services.web_capture.types import ResolvedCaptureSession


def _session() -> ResolvedCaptureSession:
    return ResolvedCaptureSession(
        mode="none",
        browser_channel=None,
        profile_path=None,
        storage_state_path=None,
    )


def test_render_page_reports_actionable_message_when_playwright_python_package_missing(
    monkeypatch,
) -> None:
    runner = PlaywrightRunner()
    monkeypatch.setattr(runner, "_get_sync_playwright", lambda: (_ for _ in ()).throw(ImportError()))

    try:
        runner.render_page(
            url="https://example.com",
            session=_session(),
            parser_name="playwright_dom",
        )
    except AppError as exc:
        assert exc.error_category == "FETCH_FAILED"
        assert "uv sync --group test" in exc.error_message
        assert "restart the backend" in exc.error_message
    else:
        raise AssertionError("Expected AppError to be raised")


def test_render_page_reports_browser_install_hint_when_playwright_browser_missing(
    monkeypatch,
) -> None:
    runner = PlaywrightRunner()

    class _FakeBrowserContext:
        def __enter__(self):
            raise RuntimeError(
                "Executable doesn't exist at C:/ms-playwright/chromium/chrome.exe\n"
                "Please run the following command to download new browsers:\n"
                "    playwright install"
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(runner, "_get_sync_playwright", lambda: (lambda: _FakeBrowserContext()))

    try:
        runner.render_page(
            url="https://example.com",
            session=_session(),
            parser_name="playwright_dom",
        )
    except AppError as exc:
        assert exc.error_category == "FETCH_FAILED"
        assert "playwright install chromium" in exc.error_message
        assert "restart the backend" in exc.error_message
    else:
        raise AssertionError("Expected AppError to be raised")


def test_render_page_retries_when_navigation_recreates_execution_context(monkeypatch) -> None:
    runner = PlaywrightRunner()

    class _FakeLocator:
        def __init__(self, index: int):
            self.index = index

        def screenshot(self, type: str = "png") -> bytes:
            assert type == "png"
            return f"image-{self.index}".encode("utf-8")

    class _FakeLocatorCollection:
        def count(self) -> int:
            return 1

        def nth(self, index: int) -> _FakeLocator:
            return _FakeLocator(index)

    class _FakePage:
        def __init__(self) -> None:
            self.evaluate_calls = 0
            self.waited_states: list[tuple[str, int | None]] = []

        def goto(self, url: str, wait_until: str, timeout: int) -> None:
            assert url == "https://example.com/protected"
            assert wait_until == "domcontentloaded"
            assert timeout == 30000

        def wait_for_timeout(self, timeout_ms: int) -> None:
            assert timeout_ms in {300, 600, 1200}

        def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
            self.waited_states.append((state, timeout))

        def evaluate(self, script: str):
            self.evaluate_calls += 1
            if self.evaluate_calls == 1:
                raise RuntimeError(
                    "Page.evaluate: Execution context was destroyed, most likely because of a navigation"
                )
            if "window.scrollTo" in script:
                return None
            return [
                {
                    "index": 0,
                    "tag_name": "img",
                    "src": "https://example.com/image.png",
                    "alt": "demo image",
                    "width": 640,
                    "height": 480,
                    "decorative": False,
                    "anchor_text": "",
                }
            ]

        def locator(self, selector: str) -> _FakeLocatorCollection:
            assert selector == "img, canvas"
            return _FakeLocatorCollection()

        def title(self) -> str:
            return "Protected page"

        def content(self) -> str:
            return "<html><body>Hello</body></html>"

    class _FakeContext:
        def __init__(self) -> None:
            self.page = _FakePage()

        def new_page(self) -> _FakePage:
            return self.page

        def close(self) -> None:
            return None

    class _FakeBrowser:
        def close(self) -> None:
            return None

    fake_context = _FakeContext()

    monkeypatch.setattr(
        runner,
        "_get_sync_playwright",
        lambda: lambda: _FakePlaywrightManager(),
    )
    monkeypatch.setattr(
        runner,
        "_open_context",
        lambda playwright, session: (fake_context, _FakeBrowser()),
    )

    result = runner.render_page(
        url="https://example.com/protected",
        session=_session(),
        parser_name="playwright_dom",
    )

    assert result["title"] == "Protected page"
    assert result["rendered_html"] == "<html><body>Hello</body></html>"
    assert result["image_elements"][0]["alt"] == "demo image"
    assert fake_context.page.evaluate_calls == 3
    assert ("domcontentloaded", 5000) in fake_context.page.waited_states
    assert ("load", 5000) in fake_context.page.waited_states
    assert ("networkidle", 2500) in fake_context.page.waited_states


class _FakePlaywrightManager:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False
