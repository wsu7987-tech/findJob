from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        error_category: str,
        error_message: str,
    ) -> None:
        self.status_code = status_code
        self.error_category = error_category
        self.error_message = error_message
        super().__init__(error_message)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_category": exc.error_category,
                "error_message": exc.error_message,
            },
        )
