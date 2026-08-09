from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, update

from incidentpilot.api.main import create_app
from incidentpilot.config import ApiSettings, AuthSettings, Settings
from incidentpilot.incidents.models import (
    AlertRow,
    AnalysisJobRow,
    EvidenceRow,
    IncidentRow,
    TenantRow,
)
from incidentpilot.runtime.database import Database
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
ALERT_TOKEN = "m5-alert-source-test-token"
OPERATOR = {"X-IncidentPilot-Actor": "local-operator"}
VIEWER = {"X-IncidentPilot-Actor": "local-viewer"}


def _app():
    return create_app(
        Settings(
            environment="development",
            api=ApiSettings(database_url=SecretStr(API_URL)),
            auth=AuthSettings(alert_source_token=SecretStr(ALERT_TOKEN)),
        )
    )


def _manual_payload(*, service: str, severity: str = "P2") -> dict[str, str]:
    return {
        "title": f"Test incident for {service}",
        "description": "M5.2 integration test",
        "severity": severity,
        "service": service,
        "starts_at": datetime.now(UTC).isoformat(),
    }


@pytest.mark.integration
async def test_alertmanager_firing_is_idempotent_and_resolved_only_appends_signal() -> None:
    fingerprint = uuid4().hex
    starts_at = datetime.now(UTC) - timedelta(minutes=1)
    alert = {
        "status": "firing",
        "labels": {
            "alertname": "CheckoutFailure",
            "service": "checkout",
            "severity": "critical",
        },
        "annotations": {
            "summary": "Checkout is failing",
            "description": "Payment calls fail",
        },
        "startsAt": starts_at.isoformat(),
        "endsAt": "0001-01-01T00:00:00Z",
        "fingerprint": fingerprint,
    }
    payload = {"version": "4", "status": "firing", "alerts": [alert]}
    app = _app()

    migration_database = Database(MIGRATION_URL)
    try:
        await seed_local_data(migration_database)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                unauthorized = await client.post("/api/v1/alerts/prometheus", json=payload)
                first = await client.post(
                    "/api/v1/alerts/prometheus",
                    json=payload,
                    headers={"Authorization": f"Bearer {ALERT_TOKEN}"},
                )
                duplicate = await client.post(
                    "/api/v1/alerts/prometheus",
                    json=payload,
                    headers={"Authorization": f"Bearer {ALERT_TOKEN}"},
                )
                alert["status"] = "resolved"
                payload["status"] = "resolved"
                resolved = await client.post(
                    "/api/v1/alerts/prometheus",
                    json=payload,
                    headers={"Authorization": f"Bearer {ALERT_TOKEN}"},
                )

        assert unauthorized.status_code == 401
        assert unauthorized.json()["code"] == "ALERT_SOURCE_UNAUTHORIZED"
        assert first.status_code == 202
        assert duplicate.status_code == 202
        assert resolved.status_code == 202
        incident_id = first.json()["incidents"][0]["incident_id"]
        assert duplicate.json()["incidents"][0]["incident_id"] == incident_id
        assert duplicate.json()["incidents"][0]["created"] is False

        async with migration_database.session_factory() as session:
            incident = await session.get(IncidentRow, incident_id)
            alert_count = await session.scalar(
                select(func.count())
                .select_from(AlertRow)
                .where(AlertRow.incident_id == incident_id)
            )
            job_count = await session.scalar(
                select(func.count())
                .select_from(AnalysisJobRow)
                .where(AnalysisJobRow.incident_id == incident_id)
            )
        assert incident is not None and incident.status == "RECEIVED"
        assert alert_count == 2
        assert job_count == 1
    finally:
        await migration_database.dispose()


@pytest.mark.integration
async def test_manual_incident_and_concurrent_analysis_keep_one_active_job() -> None:
    service = f"checkout-{uuid4().hex[:8]}"
    app = _app()
    migration_database = Database(MIGRATION_URL)
    try:
        await seed_local_data(migration_database)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                forbidden = await client.post(
                    "/api/v1/incidents", json=_manual_payload(service=service), headers=VIEWER
                )
                created = await client.post(
                    "/api/v1/incidents", json=_manual_payload(service=service), headers=OPERATOR
                )
                assert created.status_code == 201
                incident_id = created.json()["incident"]["id"]
                initial_job_id = created.json()["job_id"]

                async with migration_database.session_factory() as session, session.begin():
                    await session.execute(
                        update(AnalysisJobRow)
                        .where(AnalysisJobRow.id == initial_job_id)
                        .values(status="completed")
                    )

                first, second = await asyncio.gather(
                    client.post(f"/api/v1/incidents/{incident_id}/analysis", headers=OPERATOR),
                    client.post(f"/api/v1/incidents/{incident_id}/analysis", headers=OPERATOR),
                )

        assert forbidden.status_code == 403
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]
        assert {first.json()["created"], second.json()["created"]} == {True, False}
        async with migration_database.session_factory() as session:
            active_count = await session.scalar(
                select(func.count())
                .select_from(AnalysisJobRow)
                .where(
                    AnalysisJobRow.incident_id == incident_id,
                    AnalysisJobRow.status.in_(("queued", "running", "retry")),
                )
            )
        assert active_count == 1
    finally:
        await migration_database.dispose()


@pytest.mark.integration
async def test_manual_incident_can_wait_for_controlled_demo_telemetry_before_analysis() -> None:
    app = _app()
    migration_database = Database(MIGRATION_URL)
    try:
        await seed_local_data(migration_database)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/incidents",
                    json={**_manual_payload(service="checkout"), "start_analysis": False},
                    headers=OPERATOR,
                )

        assert response.status_code == 201
        assert response.json()["job_id"] is None
        incident_id = response.json()["incident"]["id"]
        async with migration_database.session_factory() as session:
            job_count = await session.scalar(
                select(func.count())
                .select_from(AnalysisJobRow)
                .where(AnalysisJobRow.incident_id == incident_id)
            )
        assert job_count == 0
    finally:
        await migration_database.dispose()


@pytest.mark.integration
async def test_incident_list_cursor_and_filters() -> None:
    service = f"filter-{uuid4().hex[:10]}"
    other_service = f"other-{uuid4().hex[:10]}"
    app = _app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for severity in ("P1", "P2"):
                response = await client.post(
                    "/api/v1/incidents",
                    json=_manual_payload(service=service, severity=severity),
                    headers=OPERATOR,
                )
                assert response.status_code == 201
            await client.post(
                "/api/v1/incidents",
                json=_manual_payload(service=other_service, severity="P1"),
                headers=OPERATOR,
            )

            filtered = await client.get(
                "/api/v1/incidents",
                params={"service": service, "severity": "P1", "status": "RECEIVED"},
                headers=VIEWER,
            )
            first_page = await client.get(
                "/api/v1/incidents", params={"service": service, "limit": 1}, headers=VIEWER
            )
            second_page = await client.get(
                "/api/v1/incidents",
                params={
                    "service": service,
                    "limit": 1,
                    "cursor": first_page.json()["next_cursor"],
                    "created_from": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
                    "created_to": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                },
                headers=VIEWER,
            )
            invalid_cursor = await client.get(
                "/api/v1/incidents",
                params={"cursor": "%%%not-a-cursor%%%"},
                headers=VIEWER,
            )

    assert filtered.status_code == 200
    assert len(filtered.json()["items"]) == 1
    assert filtered.json()["items"][0]["service"] == service
    assert first_page.status_code == 200 and second_page.status_code == 200
    assert first_page.json()["next_cursor"]
    assert first_page.json()["items"][0]["id"] != second_page.json()["items"][0]["id"]
    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json()["code"] == "INVALID_CURSOR_OR_TIME_RANGE"


@pytest.mark.integration
async def test_evidence_detail_enforces_tenant_ownership_and_redacts_raw_data() -> None:
    local_incident = f"inc-api-{uuid4().hex}"
    other_incident = f"inc-api-{uuid4().hex}"
    local_evidence = f"ev-api-{uuid4().hex}"
    other_evidence = f"ev-api-{uuid4().hex}"
    other_tenant = f"tenant-{uuid4().hex}"
    now = datetime.now(UTC)
    migration_database = Database(MIGRATION_URL)
    try:
        await seed_local_data(migration_database)
        async with migration_database.session_factory() as session, session.begin():
            session.add(TenantRow(id=other_tenant, name=other_tenant))
            await session.flush()
            session.add_all(
                [
                    IncidentRow(
                        id=local_incident,
                        tenant_id="local",
                        source="evidence-test",
                        external_id=local_incident,
                        status="RECEIVED",
                        severity="P2",
                        title="Local evidence",
                    ),
                    IncidentRow(
                        id=other_incident,
                        tenant_id=other_tenant,
                        source="evidence-test",
                        external_id=other_incident,
                        status="RECEIVED",
                        severity="P2",
                        title="Other evidence",
                    ),
                ]
            )
            await session.flush()
            for evidence_id, incident_id in (
                (local_evidence, local_incident),
                (other_evidence, other_incident),
            ):
                session.add(
                    EvidenceRow(
                        id=evidence_id,
                        incident_id=incident_id,
                        kind="log",
                        source_system="test",
                        summary="Sensitive test evidence",
                        query_json={"template_id": "test"},
                        raw_json={
                            "authorization": "Bearer secret-token",
                            "message": "user@example.com used 4111 1111 1111 1111",
                        },
                        digest=uuid4().hex.ljust(64, "0"),
                        source_uri="test://evidence",
                        observed_start=now,
                        observed_end=now,
                        truncated=False,
                        collected_at=now,
                    )
                )

        app = _app()
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                detail = await client.get(
                    f"/api/v1/incidents/{local_incident}/evidence/{local_evidence}",
                    headers=VIEWER,
                )
                cross_tenant = await client.get(
                    f"/api/v1/incidents/{other_incident}/evidence/{other_evidence}",
                    headers=VIEWER,
                )

        assert detail.status_code == 200
        assert detail.json()["raw_json"] == {
            "authorization": "[REDACTED]",
            "message": "[REDACTED_EMAIL] used [REDACTED_PAYMENT]",
        }
        assert cross_tenant.status_code == 404
    finally:
        await migration_database.dispose()
