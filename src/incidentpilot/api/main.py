from __future__ import annotations

import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import Response

from incidentpilot.api.errors import install_problem_handlers
from incidentpilot.api.routes.alerts import router as alerts_router
from incidentpilot.api.routes.approvals import router as approvals_router
from incidentpilot.api.routes.evaluations import router as evaluations_router
from incidentpilot.api.routes.evolution import router as evolution_router
from incidentpilot.api.routes.health import router as health_router
from incidentpilot.api.routes.incidents import router as incidents_router
from incidentpilot.config import Settings
from incidentpilot.observability.setup import instrument_fastapi
from incidentpilot.runtime.lifespan import api_lifespan

CORRELATION_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with api_lifespan(resolved_settings) as runtime:
            app.state.runtime = runtime
            instrument_fastapi(app, runtime.tracer_provider)
            yield
            del app.state.runtime

    app = FastAPI(title="IncidentPilot API", version="0.1.0", lifespan=lifespan)
    install_problem_handlers(app)

    async def correlation_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied = request.headers.get("x-correlation-id", "")
        request.state.correlation_id = (
            supplied if CORRELATION_ID.fullmatch(supplied) else uuid4().hex
        )
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    app.middleware("http")(correlation_id)
    for router in (
        health_router,
        alerts_router,
        incidents_router,
        approvals_router,
        evaluations_router,
        evolution_router,
    ):
        app.include_router(router, prefix="/api/v1")

    return app


app = create_app()
