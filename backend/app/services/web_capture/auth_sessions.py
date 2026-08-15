from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.errors import AppError

from .types import ResolvedCaptureSession


def resolve_capture_session(preferred_profile: dict[str, Any] | None) -> ResolvedCaptureSession:
    if preferred_profile is None:
        return ResolvedCaptureSession(
            mode="none",
            browser_channel=None,
            profile_path=None,
            storage_state_path=None,
        )

    profile_path = preferred_profile.get("profile_path")
    storage_state_path = preferred_profile.get("storage_state_path")
    browser_channel = str(preferred_profile.get("browser_channel") or "chromium")

    if profile_path and Path(str(profile_path)).exists():
        return ResolvedCaptureSession(
            mode="browser_profile",
            browser_channel=browser_channel,
            profile_path=str(profile_path),
            storage_state_path=None,
        )

    if storage_state_path and Path(str(storage_state_path)).exists():
        return ResolvedCaptureSession(
            mode="storage_state",
            browser_channel=browser_channel,
            profile_path=None,
            storage_state_path=str(storage_state_path),
        )

    raise AppError(
        status_code=400,
        error_category="AUTH_REQUIRED",
        error_message="No usable authentication session was found.",
    )
