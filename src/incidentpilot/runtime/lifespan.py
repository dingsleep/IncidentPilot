from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy import select, text

from incidentpilot.api.auth import AlertSourceAuthenticator, AuthAdapter, build_auth_adapter
from incidentpilot.auth.tokens import DevelopmentApprovalGrantProvider
from incidentpilot.config import Settings
from incidentpilot.incidents.models import ServiceHeartbeatRow
from incidentpilot.incidents.service import IncidentService
from incidentpilot.observability.metrics import OperationalMetrics
from incidentpilot.observability.setup import (
    create_meter_provider,
    create_tracer_provider,
    instrument_sqlalchemy,
)
from incidentpilot.remediation.approval_service import ApprovalService
from incidentpilot.runtime.database import Database

HEARTBEAT_MAX_AGE = timedelta(seconds=90)
PROCESS_NAMES = {
    "worker": "worker",
    "telemetry_mcp": "telemetry-mcp",
    "action_mcp": "action-mcp",
}


class HealthRepository:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._clock = clock or (lambda: datetime.now(UTC))

    async def readiness(self, *, action_enabled: bool) -> dict[str, Any]:
        checks: dict[str, dict[str, str]] = {
            "api_db": await self._query_check("SELECT 1"),
            "job_queue": await self._query_check("SELECT 1 FROM analysis_jobs LIMIT 1"),
        }
        checks.update(await self._process_checks(action_enabled=action_enabled))
        ready = all(check["status"] in {"ready", "disabled"} for check in checks.values())
        return {"status": "ready" if ready else "not_ready", "checks": checks}

    async def _query_check(self, statement: str) -> dict[str, str]:
        try:
            async with self._database.engine.connect() as connection:
                await connection.execute(text(statement))
        except Exception:
            return {"status": "not_ready"}
        return {"status": "ready"}

    async def _process_checks(self, *, action_enabled: bool) -> dict[str, dict[str, str]]:
        names = [PROCESS_NAMES["worker"], PROCESS_NAMES["telemetry_mcp"]]
        if action_enabled:
            names.append(PROCESS_NAMES["action_mcp"])
        rows: Sequence[ServiceHeartbeatRow] = ()
        try:
            async with self._database.session_factory() as session:
                result = await session.scalars(
                    select(ServiceHeartbeatRow)
                    .where(ServiceHeartbeatRow.process_name.in_(names))
                    .order_by(ServiceHeartbeatRow.last_seen_at.desc())
                )
                rows = result.all()
        except Exception:
            rows = ()

        newest = {row.process_name: row for row in reversed(rows)}
        now = self._clock()
        checks = {
            key: {
                "status": (
                    "ready"
                    if (row := newest.get(process_name)) is not None
                    and row.status == "ready"
                    and now - row.last_seen_at <= HEARTBEAT_MAX_AGE
                    else "not_ready"
                )
            }
            for key, process_name in PROCESS_NAMES.items()
            if key != "action_mcp" or action_enabled
        }
        if not action_enabled:
            checks["action_mcp"] = {"status": "disabled"}
        return checks


@dataclass(frozen=True)
class ApiRuntime:
    database: Database
    auth: AuthAdapter
    alert_source_auth: AlertSourceAuthenticator
    tracer_provider: TracerProvider
    health_repository: HealthRepository
    incidents: IncidentService
    approvals: ApprovalService | None
    action_enabled: bool


@asynccontextmanager
async def api_lifespan(settings: Settings) -> AsyncGenerator[ApiRuntime]:
    if settings.api.database_url is None:
        raise RuntimeError("INCIDENTPILOT_API_DATABASE_URL is required")

    database = Database(settings.api.database_url.get_secret_value())
    try:
        auth = build_auth_adapter(environment=settings.environment, settings=settings.auth)
        alert_source_auth = AlertSourceAuthenticator(settings.auth.alert_source_token)
        tracer_provider = create_tracer_provider("incidentpilot-api")
        meter_provider = create_meter_provider("incidentpilot-api")
        instrument_sqlalchemy(database.engine, tracer_provider)
        health_repository = HealthRepository(database)
        incidents = IncidentService(database)
        signing_key = settings.actions.approval_signing_key
        approvals = (
            ApprovalService(
                database=database,
                grants=DevelopmentApprovalGrantProvider(
                    issuer=settings.actions.approval_issuer,
                    audience=settings.actions.approval_audience,
                    private_key=signing_key.get_secret_value().replace("\\n", "\n"),
                ),
                operational_metrics=OperationalMetrics(meter_provider),
            )
            if signing_key is not None
            else None
        )
        try:
            yield ApiRuntime(
                database=database,
                auth=auth,
                alert_source_auth=alert_source_auth,
                tracer_provider=tracer_provider,
                health_repository=health_repository,
                incidents=incidents,
                approvals=approvals,
                action_enabled=settings.actions.enabled,
            )
        finally:
            tracer_provider.shutdown()
            meter_provider.shutdown()
    finally:
        await database.dispose()
