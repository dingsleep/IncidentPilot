from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class ApiProblem(Exception):
    def __init__(self, *, status: int, code: str, title: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiProblem, _api_problem_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, _http_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _internal_handler)


def _correlation_id(request: Request) -> str:
    return str(getattr(request.state, "correlation_id", "unknown"))


def _response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
) -> JSONResponse:
    correlation_id = _correlation_id(request)
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        headers={"X-Correlation-ID": correlation_id},
        content={
            "type": "about:blank",
            "title": title,
            "status": status,
            "detail": detail,
            "code": code,
            "correlation_id": correlation_id,
        },
    )


async def _api_problem_handler(request: Request, exc: ApiProblem) -> JSONResponse:
    return _response(
        request,
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
    )


async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    del exc
    return _response(
        request,
        status=422,
        code="VALIDATION_ERROR",
        title="Unprocessable Content",
        detail="The request did not match the required schema.",
    )


async def _http_handler(request: Request, exc: HTTPException) -> JSONResponse:
    try:
        title = HTTPStatus(exc.status_code).phrase
    except ValueError:
        title = "HTTP Error"
    return _response(
        request,
        status=exc.status_code,
        code=f"HTTP_{exc.status_code}",
        title=title,
        detail="The requested operation could not be completed.",
    )


async def _internal_handler(request: Request, exc: Exception) -> JSONResponse:
    del exc
    return _response(
        request,
        status=500,
        code="INTERNAL_ERROR",
        title="Internal Server Error",
        detail="The server could not complete the request.",
    )
