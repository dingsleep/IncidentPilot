from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Self

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from incidentpilot.api.dependencies import get_runtime
from incidentpilot.domain.alerts import AlertPayload
from incidentpilot.domain.enums import Severity

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    status: Literal["firing", "resolved"]
    labels: dict[str, str] = Field(default_factory=dict, max_length=100)
    annotations: dict[str, str] = Field(default_factory=dict, max_length=100)
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    fingerprint: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def require_aware_times(self) -> Self:
        if self.starts_at.utcoffset() is None or (
            self.ends_at is not None and self.ends_at.utcoffset() is None
        ):
            raise ValueError("Alertmanager timestamps must be timezone-aware")
        return self


class AlertmanagerWebhook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: str
    status: Literal["firing", "resolved"]
    alerts: list[AlertmanagerAlert] = Field(min_length=1, max_length=100)


async def ingest_prometheus(payload: AlertmanagerWebhook, request: Request) -> dict[str, object]:
    runtime = get_runtime(request)
    runtime.alert_source_auth.authenticate(request.headers)
    results: list[dict[str, Any]] = []
    for item in payload.alerts:
        labels = {
            **item.labels,
            "alertmanager_status": item.status,
            "alertmanager_fingerprint": item.fingerprint,
        }
        alert = AlertPayload(
            external_id=f"{item.fingerprint}:{item.starts_at.astimezone(UTC).isoformat()}",
            source="prometheus-alertmanager",
            title=item.annotations.get("summary") or item.labels.get("alertname") or "Alert",
            description=item.annotations.get("description", ""),
            severity=_severity(item.labels.get("severity")),
            starts_at=item.starts_at,
            service_hint=_service(item.labels),
            labels=labels,
            annotations=item.annotations,
        )
        results.append(
            (
                await runtime.incidents.ingest_alert_signal(
                    tenant_id="local",
                    alert=alert,
                    signal_status=item.status,
                )
            ).model_dump(mode="json")
        )
    return {"accepted": len(results), "incidents": results}


def _severity(value: str | None) -> Severity:
    mapping = {
        "critical": Severity.P1,
        "warning": Severity.P2,
        "info": Severity.P3,
    }
    try:
        return Severity(value)
    except (TypeError, ValueError):
        return mapping.get((value or "").casefold(), Severity.P3)


def _service(labels: dict[str, str]) -> str | None:
    return labels.get("service") or labels.get("service_name") or labels.get("service.name")


router.add_api_route("/prometheus", ingest_prometheus, methods=["POST"], status_code=202)
