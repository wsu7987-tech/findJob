from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ResolvedCaptureSession:
    mode: str
    browser_channel: str | None
    profile_path: str | None
    storage_state_path: str | None
