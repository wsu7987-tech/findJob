from __future__ import annotations

from pathlib import Path


def test_resolve_session_prefers_browser_profile_when_available(monkeypatch) -> None:
    import backend.app.services.web_capture.auth_sessions as auth_sessions

    profile_dir = "C:/profiles/chrome-profile"
    storage_state = "C:/profiles/session.json"

    monkeypatch.setattr(
        auth_sessions.Path,
        "exists",
        lambda self: self.as_posix() == Path(profile_dir).as_posix(),
    )

    resolved = auth_sessions.resolve_capture_session(
        preferred_profile={
            "id": "profile-1",
            "mode": "browser_profile",
            "browser_channel": "chrome",
            "profile_path": profile_dir,
            "storage_state_path": storage_state,
        }
    )

    assert resolved.mode == "browser_profile"
    assert resolved.browser_channel == "chrome"
    assert resolved.storage_state_path is None


def test_resolve_session_defaults_browser_channel_to_chromium(monkeypatch) -> None:
    import backend.app.services.web_capture.auth_sessions as auth_sessions

    profile_dir = "C:/profiles/chrome-profile"

    monkeypatch.setattr(
        auth_sessions.Path,
        "exists",
        lambda self: self.as_posix() == Path(profile_dir).as_posix(),
    )

    resolved = auth_sessions.resolve_capture_session(
        preferred_profile={
            "id": "profile-1",
            "mode": "browser_profile",
            "profile_path": profile_dir,
        }
    )

    assert resolved.mode == "browser_profile"
    assert resolved.browser_channel == "chromium"
    assert resolved.storage_state_path is None


def test_resolve_session_falls_back_to_storage_state(monkeypatch) -> None:
    import backend.app.services.web_capture.auth_sessions as auth_sessions

    profile_dir = "C:/profiles/missing"
    storage_state = "C:/profiles/session.json"

    monkeypatch.setattr(
        auth_sessions.Path,
        "exists",
        lambda self: self.as_posix() == Path(storage_state).as_posix(),
    )

    resolved = auth_sessions.resolve_capture_session(
        preferred_profile={
            "id": "profile-1",
            "mode": "browser_profile",
            "browser_channel": "chrome",
            "profile_path": profile_dir,
            "storage_state_path": storage_state,
        }
    )

    assert resolved.mode == "storage_state"
    assert resolved.storage_state_path == storage_state


def test_resolve_session_defaults_browser_channel_to_chromium_for_storage_state(
    monkeypatch,
) -> None:
    import backend.app.services.web_capture.auth_sessions as auth_sessions

    storage_state = "C:/profiles/session.json"

    monkeypatch.setattr(
        auth_sessions.Path,
        "exists",
        lambda self: self.as_posix() == Path(storage_state).as_posix(),
    )

    resolved = auth_sessions.resolve_capture_session(
        preferred_profile={
            "id": "profile-1",
            "mode": "browser_profile",
            "storage_state_path": storage_state,
        }
    )

    assert resolved.mode == "storage_state"
    assert resolved.browser_channel == "chromium"
    assert resolved.storage_state_path == storage_state


def test_resolve_session_returns_none_mode_when_no_profile() -> None:
    from backend.app.services.web_capture.auth_sessions import resolve_capture_session

    resolved = resolve_capture_session(None)

    assert resolved.mode == "none"
    assert resolved.browser_channel is None
    assert resolved.profile_path is None
    assert resolved.storage_state_path is None


def test_resolve_session_raises_auth_required_when_nothing_available(monkeypatch) -> None:
    import backend.app.services.web_capture.auth_sessions as auth_sessions

    monkeypatch.setattr(auth_sessions.Path, "exists", lambda self: False)

    try:
        auth_sessions.resolve_capture_session(
            preferred_profile={
                "id": "profile-1",
                "mode": "browser_profile",
                "browser_channel": "chrome",
                "profile_path": "C:/profiles/missing",
                "storage_state_path": "C:/profiles/missing.json",
            }
        )
    except auth_sessions.AppError as exc:
        assert exc.error_category == "AUTH_REQUIRED"
    else:
        raise AssertionError("Expected AppError to be raised")
