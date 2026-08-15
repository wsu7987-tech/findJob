from __future__ import annotations

from typing import Protocol


class WebCaptureHandler(Protocol):
    def supports(self, *, url: str, parser_name: str) -> bool: ...

    def capture_url(
        self,
        *,
        url: str,
        parser_name: str,
        session_profile_id: str | None,
        cancel_check=None,
    ) -> dict[str, object]: ...
