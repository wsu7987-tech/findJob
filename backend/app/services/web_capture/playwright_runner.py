from __future__ import annotations

import base64
from typing import Any

from backend.app.errors import AppError
from backend.app.services.web_capture.types import ResolvedCaptureSession


class PlaywrightRunner:
    _NAVIGATION_RETRY_ATTEMPTS = 3

    def render_page(
        self,
        *,
        url: str,
        session: ResolvedCaptureSession,
        parser_name: str,
        cancel_check=None,
    ) -> dict[str, Any]:
        del parser_name
        self._raise_if_cancelled(cancel_check)
        try:
            sync_playwright = self._get_sync_playwright()
        except ImportError as exc:  # pragma: no cover - depends on local runtime install
            raise AppError(
                status_code=500,
                error_category="FETCH_FAILED",
                error_message=(
                    "Playwright Python package is not available in the backend runtime. "
                    "Run `uv sync --group test`, then restart the backend from `.venv`."
                ),
            ) from exc

        try:
            with sync_playwright() as playwright:
                context, browser = self._open_context(playwright, session=session)
                try:
                    page = context.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    self._wait_for_document_stable(page)
                    page.wait_for_timeout(1200)
                    self._raise_if_cancelled(cancel_check)
                    self._run_with_navigation_retry(
                        page,
                        lambda: page.evaluate(
                            """
                            () => {
                              window.scrollTo(0, document.body.scrollHeight || 0);
                            }
                            """
                        ),
                        cancel_check=cancel_check,
                    )
                    page.wait_for_timeout(600)
                    self._wait_for_document_stable(page)
                    self._raise_if_cancelled(cancel_check)
                    image_elements = self._run_with_navigation_retry(
                        page,
                        lambda: page.evaluate(
                            """
                            () => {
                              const collectAnchorText = (element) => {
                                const figure = element.closest("figure");
                                if (figure) {
                                  const caption = figure.querySelector("figcaption");
                                  if (caption?.textContent?.trim()) {
                                    return caption.textContent.trim();
                                  }
                                }
                                const parent = element.parentElement;
                                if (parent?.textContent?.trim()) {
                                  return parent.textContent.trim().slice(0, 300);
                                }
                                return "";
                              };

                              return Array.from(document.querySelectorAll("img, canvas")).map((element, index) => ({
                                index,
                                tag_name: element.tagName.toLowerCase(),
                                src: element instanceof HTMLImageElement ? element.currentSrc || element.src : null,
                                alt: element instanceof HTMLImageElement ? element.alt || "" : "",
                                width: element.clientWidth || element.width || 0,
                                height: element.clientHeight || element.height || 0,
                                decorative: element.getAttribute("aria-hidden") === "true" || element.getAttribute("role") === "presentation",
                                anchor_text: collectAnchorText(element)
                              }));
                            }
                            """
                        ),
                        cancel_check=cancel_check,
                    )
                    locators = page.locator("img, canvas")
                    count = locators.count()
                    enriched_elements: list[dict[str, Any]] = []
                    for index in range(count):
                        metadata = image_elements[index] if index < len(image_elements) else {"index": index}
                        try:
                            screenshot_bytes = locators.nth(index).screenshot(type="png")
                            metadata["screenshot_base64"] = base64.b64encode(screenshot_bytes).decode("ascii")
                        except Exception:
                            metadata["screenshot_base64"] = None
                        enriched_elements.append(metadata)
                    return {
                        "title": self._run_with_navigation_retry(
                            page,
                            page.title,
                            cancel_check=cancel_check,
                        ),
                        "rendered_html": self._run_with_navigation_retry(
                            page,
                            page.content,
                            cancel_check=cancel_check,
                        ),
                        "image_elements": enriched_elements,
                    }
                finally:
                    context.close()
                    if browser is not None:
                        browser.close()
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - depends on site/browser runtime
            raise AppError(
                status_code=502,
                error_category="FETCH_FAILED",
                error_message=self._map_runtime_error(exc),
            ) from exc

    @staticmethod
    def _open_context(playwright, *, session: ResolvedCaptureSession):
        browser_type = playwright.chromium
        channel = None if session.browser_channel in {None, "chromium"} else session.browser_channel

        if session.mode == "browser_profile" and session.profile_path:
            context = browser_type.launch_persistent_context(
                user_data_dir=session.profile_path,
                channel=channel,
                headless=True,
            )
            return context, None

        browser = browser_type.launch(channel=channel, headless=True)
        if session.mode == "storage_state" and session.storage_state_path:
            context = browser.new_context(storage_state=session.storage_state_path)
        else:
            context = browser.new_context()
        return context, browser

    @staticmethod
    def _raise_if_cancelled(cancel_check) -> None:
        if callable(cancel_check) and cancel_check():
            raise AppError(
                status_code=409,
                error_category="CANCELLED",
                error_message="Web capture was cancelled.",
            )

    @staticmethod
    def _get_sync_playwright():
        from playwright.sync_api import sync_playwright

        return sync_playwright

    def _run_with_navigation_retry(self, page, operation, *, cancel_check=None):
        for attempt in range(self._NAVIGATION_RETRY_ATTEMPTS):
            self._raise_if_cancelled(cancel_check)
            try:
                return operation()
            except Exception as exc:
                if not self._is_navigation_context_error(exc):
                    raise
                if attempt == self._NAVIGATION_RETRY_ATTEMPTS - 1:
                    raise
                self._wait_for_document_stable(page)
        raise RuntimeError("Navigation retry loop exited unexpectedly.")

    @staticmethod
    def _wait_for_document_stable(page) -> None:
        for state, timeout in (("domcontentloaded", 5000), ("load", 5000), ("networkidle", 2500)):
            try:
                page.wait_for_load_state(state, timeout=timeout)
            except Exception:
                continue
        page.wait_for_timeout(300)

    @staticmethod
    def _is_navigation_context_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "execution context was destroyed" in message or "most likely because of a navigation" in message

    @staticmethod
    def _map_runtime_error(exc: Exception) -> str:
        message = str(exc).strip()
        lowered = message.lower()

        if "executable doesn't exist" in lowered or "download new browsers" in lowered:
            return (
                "Playwright browser runtime is not installed. "
                "Run `.venv\\Scripts\\python -m playwright install chromium`, then restart the backend."
            )

        if isinstance(exc, PermissionError):
            return (
                "Playwright could not start the browser process because the current runtime "
                "does not have permission to launch it."
            )

        if "execution context was destroyed" in lowered or "most likely because of a navigation" in lowered:
            return (
                "Failed to render page because the site kept navigating during capture. "
                "Finish the login or redirect flow, then retry."
            )

        return f"Failed to render page: {message}"
