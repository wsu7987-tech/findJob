from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        error_category: str,
        error_message: str,
    ) -> None:
        self.status_code = status_code
        self.error_category = error_category
        self.error_message = error_message
        super().__init__(error_message)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def handle_invalid_start(request, exc):
        body = exc.body
        operation_id = body.get("operation_id") if isinstance(body, dict) else None
        if operation_id and "/fine-job/" in request.url.path:
            from backend.app.services.fine_job.collection_start_operations import resolve_operation
            try:
                resolve_operation(request.app.state.db, str(operation_id))
            except AppError:
                pass
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(AppError)
    async def handle_app_error(_, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_category": exc.error_category,
                "error_message": exc.error_message,
                **({"operation_id": exc.operation_id} if hasattr(exc, "operation_id") else {}),
            },
        )
