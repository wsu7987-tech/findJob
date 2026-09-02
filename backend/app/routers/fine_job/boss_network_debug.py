from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.config import AppConfig
from backend.app.dependencies import get_config
from backend.app.errors import AppError
from backend.app.schemas.fine_job.boss_network_debug import BossNetworkDebugResponse
from backend.app.services.fine_job.boss_network_debug import boss_network_debug_manager


router = APIRouter(prefix="/fine-job/boss-network-debug", tags=["fine-job-boss-network-debug"])


@router.get("/status", response_model=BossNetworkDebugResponse)
def get_boss_network_debug_status() -> BossNetworkDebugResponse:
    return BossNetworkDebugResponse(**boss_network_debug_manager.status())


@router.post("/start", response_model=BossNetworkDebugResponse)
def start_boss_network_debug(
    config: AppConfig = Depends(get_config),
) -> BossNetworkDebugResponse:
    try:
        result = boss_network_debug_manager.start(
            config.output_root / "fine-job" / "cdp-network-debug"
        )
    except RuntimeError as exc:
        raise AppError(
            status_code=409,
            error_category="BOSS_NETWORK_DEBUG_UNAVAILABLE",
            error_message=str(exc),
        ) from exc
    return BossNetworkDebugResponse(**result)


@router.post("/stop", response_model=BossNetworkDebugResponse)
def stop_boss_network_debug() -> BossNetworkDebugResponse:
    return BossNetworkDebugResponse(**boss_network_debug_manager.stop())
