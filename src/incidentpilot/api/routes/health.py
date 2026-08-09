from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from incidentpilot.api.dependencies import get_runtime

router = APIRouter(prefix="/health", tags=["health"])


async def live() -> dict[str, str]:
    return {"status": "live"}


async def ready(request: Request) -> JSONResponse:
    runtime = get_runtime(request)
    report = await runtime.health_repository.readiness(action_enabled=runtime.action_enabled)
    return JSONResponse(report, status_code=200 if report["status"] == "ready" else 503)


router.add_api_route("/live", live, methods=["GET"])
router.add_api_route("/ready", ready, methods=["GET"])
