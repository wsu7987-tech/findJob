from __future__ import annotations

from fastapi import APIRouter

from backend.app.schemas.health import HealthResponse


router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="knowledge-curator",
        version="0.1.0",
    )
