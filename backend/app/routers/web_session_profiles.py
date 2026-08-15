from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.config import AppConfig
from backend.app.dependencies import get_config, get_web_session_profile_store
from backend.app.schemas.web_session_profiles import (
    WebSessionProfileCreateRequest,
    WebSessionProfileDeleteResponse,
    WebSessionProfileEnvelope,
    WebSessionProfileListEnvelope,
    WebSessionProfileLoginRequest,
    WebSessionProfileResponse,
    WebSessionProfileUpdateRequest,
)
from backend.app.services.web_session_profiles import (
    WebSessionProfileService,
    WebSessionProfileStore,
    run_managed_session_login,
)


router = APIRouter(prefix="/web/session-profiles", tags=["web-session-profiles"])


@router.get("", response_model=WebSessionProfileListEnvelope)
def list_web_session_profiles(
    config: AppConfig = Depends(get_config),
    store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebSessionProfileListEnvelope:
    service = WebSessionProfileService(store=store, app_data_dir=config.app_data_dir)
    return WebSessionProfileListEnvelope(
        profiles=[WebSessionProfileResponse(**item) for item in service.list_profiles()]
    )


@router.post("", response_model=WebSessionProfileEnvelope, status_code=status.HTTP_201_CREATED)
def create_web_session_profile(
    payload: WebSessionProfileCreateRequest,
    config: AppConfig = Depends(get_config),
    store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebSessionProfileEnvelope:
    service = WebSessionProfileService(store=store, app_data_dir=config.app_data_dir)
    return WebSessionProfileEnvelope(
        profile=WebSessionProfileResponse(
            **service.create_profile(
                name=payload.name,
                mode=payload.mode,
                browser_channel=payload.browser_channel,
                profile_path=payload.profile_path,
                login_url=payload.login_url,
            )
        )
    )


@router.patch("/{profile_id}", response_model=WebSessionProfileEnvelope)
def update_web_session_profile(
    profile_id: str,
    payload: WebSessionProfileUpdateRequest,
    config: AppConfig = Depends(get_config),
    store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebSessionProfileEnvelope:
    service = WebSessionProfileService(store=store, app_data_dir=config.app_data_dir)
    return WebSessionProfileEnvelope(
        profile=WebSessionProfileResponse(
            **service.update_profile(
                profile_id,
                name=payload.name,
                browser_channel=payload.browser_channel,
                profile_path=payload.profile_path,
                login_url=payload.login_url,
            )
        )
    )


@router.post("/{profile_id}/login", response_model=WebSessionProfileEnvelope, status_code=status.HTTP_202_ACCEPTED)
def start_web_session_profile_login(
    profile_id: str,
    payload: WebSessionProfileLoginRequest,
    config: AppConfig = Depends(get_config),
    store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebSessionProfileEnvelope:
    service = WebSessionProfileService(
        store=store,
        app_data_dir=config.app_data_dir,
        login_runner=run_managed_session_login,
    )
    return WebSessionProfileEnvelope(
        profile=WebSessionProfileResponse(
            **service.start_managed_login(profile_id, login_url=payload.login_url)
        )
    )


@router.delete("/{profile_id}", response_model=WebSessionProfileDeleteResponse)
def delete_web_session_profile(
    profile_id: str,
    config: AppConfig = Depends(get_config),
    store: WebSessionProfileStore = Depends(get_web_session_profile_store),
) -> WebSessionProfileDeleteResponse:
    service = WebSessionProfileService(store=store, app_data_dir=config.app_data_dir)
    return WebSessionProfileDeleteResponse(deleted=service.delete_profile(profile_id))
