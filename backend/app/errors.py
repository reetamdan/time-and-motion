"""Stable, structured API errors."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}},
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def handle_api_error(_: Request, exc: APIError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"location": [str(part) for part in item["loc"]], "message": item["msg"], "type": item["type"]}
            for item in exc.errors()
        ]
        return error_response(422, "VALIDATION_ERROR", "Request validation failed", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 503: "UNAVAILABLE"}
        return error_response(
            exc.status_code,
            codes.get(exc.status_code, "HTTP_ERROR"),
            str(exc.detail),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Do not leak local paths, SQL, credentials, or stack traces to clients.
        import logging

        logging.getLogger("cctv.api").exception("Unhandled API error", exc_info=exc)
        return error_response(500, "INTERNAL_ERROR", "An internal error occurred")
