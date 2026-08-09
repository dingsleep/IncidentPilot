from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from incidentpilot.api.main import create_app
from incidentpilot.api.sse import SSE_BUFFER_LIMIT, SseRepository, stream_events
from incidentpilot.config import ApiSettings, Settings
from incidentpilot.incidents.models import IncidentRow, TenantRow
from incidentpilot.incidents.timeline import AuditTimeline
from incidentpilot.runtime.database import Database
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
VIEWER = {"X-IncidentPilot-Actor": "local-viewer"}


def _app():
    return create_app(
        Settings(
            environment="development",
            api=ApiSettings(database_url=SecretStr(API_URL)),
        )
    )


async def _create_incident(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/incidents",
        json={
            "title": "SSE integration test",
            "severity": "P2",
            "service": "checkout",
            "starts_at": datetime.now(UTC).isoformat(),
        },
        headers={"X-IncidentPilot-Actor": "local-operator"},
    )
    assert response.status_code == 201
    return str(response.json()["incident"]["id"])


async def _append_events(database: Database, incident_id: str) -> None:
    async with database.session_factory() as session, session.begin():
        timeline = AuditTimeline(session)
        for index, event_type in enumerate(
            ("run.started", "agent.completed", "diagnosis.created"),
            start=1,
        ):
            await timeline.append(
                event_id=f"audit-sse-{uuid4().hex}",
                tenant_id="local",
                incident_id=incident_id,
                actor_type="worker",
                actor_id="sse-test",
                event_type=event_type,
                payload={"sequence": index},
            )


def _parse(chunk: str) -> tuple[str, str, dict[str, object]]:
    fields = dict(line.split(": ", 1) for line in chunk.strip().splitlines())
    return fields["id"], fields["event"], cast(dict[str, object], json.loads(fields["data"]))


@pytest.mark.integration
async def test_sse_reconnect_is_monotonic_without_loss_or_duplicates() -> None:
    api_database = Database(API_URL)
    app = _app()
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                incident_id = await _create_incident(client)
            await _append_events(api_database, incident_id)

            repository = SseRepository(api_database)
            first_stream = stream_events(
                repository,
                tenant_id="local",
                incident_id=incident_id,
                poll_interval_seconds=0.01,
                heartbeat_seconds=1,
            )
            assert await anext(first_stream) == ": connected\n\n"
            first_chunk = await anext(first_stream)
            first_id, first_type, first_data = _parse(first_chunk)
            assert first_type == "run.started"
            assert first_data["schema_version"] == 1
            assert cast(Any, api_database.engine.pool).checkedout() == 0
            await first_stream.aclose()

            resumed = stream_events(
                repository,
                tenant_id="local",
                incident_id=incident_id,
                last_event_id=first_id,
                poll_interval_seconds=0.01,
                heartbeat_seconds=1,
            )
            assert await anext(resumed) == ": connected\n\n"
            second = _parse(await anext(resumed))
            third = _parse(await anext(resumed))
            await resumed.aclose()

        assert first_id < second[0] < third[0]
        assert [first_type, second[1], third[1]] == [
            "run.started",
            "agent.completed",
            "diagnosis.created",
        ]
        assert [first_data["payload"], second[2]["payload"], third[2]["payload"]] == [
            {"sequence": 1},
            {"sequence": 2},
            {"sequence": 3},
        ]
    finally:
        await api_database.dispose()


@pytest.mark.integration
async def test_sse_heartbeat_and_batch_buffer_are_bounded() -> None:
    api_database = Database(API_URL)
    app = _app()
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                incident_id = await _create_incident(client)

            heartbeat_stream = stream_events(
                SseRepository(api_database),
                tenant_id="local",
                incident_id=incident_id,
                poll_interval_seconds=0.005,
                heartbeat_seconds=0.02,
            )
            connected = await asyncio.wait_for(anext(heartbeat_stream), timeout=0.2)
            heartbeat = await asyncio.wait_for(anext(heartbeat_stream), timeout=0.2)
            await heartbeat_stream.aclose()

            with pytest.raises(ValueError, match="buffer limit"):
                await SseRepository(api_database).fetch(
                    tenant_id="local",
                    incident_id=incident_id,
                    after=None,
                    limit=SSE_BUFFER_LIMIT + 1,
                )

        assert connected == ": connected\n\n"
        assert heartbeat == ": heartbeat\n\n"
        assert cast(Any, api_database.engine.pool).checkedout() == 0
    finally:
        await api_database.dispose()


@pytest.mark.integration
async def test_sse_rejects_missing_actor_cross_tenant_and_invalid_last_event_id() -> None:
    other_tenant = f"tenant-sse-{uuid4().hex}"
    other_incident = f"inc-sse-{uuid4().hex}"
    migration_database = Database(MIGRATION_URL)
    try:
        await seed_local_data(migration_database)
        async with migration_database.session_factory() as session, session.begin():
            session.add(TenantRow(id=other_tenant, name=other_tenant))
            await session.flush()
            session.add(
                IncidentRow(
                    id=other_incident,
                    tenant_id=other_tenant,
                    source="sse-test",
                    external_id=other_incident,
                    status="RECEIVED",
                    severity="P2",
                    title="Cross tenant SSE",
                )
            )

        app = _app()
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                missing_actor = await client.get(f"/api/v1/incidents/{other_incident}/events")
                cross_tenant = await client.get(
                    f"/api/v1/incidents/{other_incident}/events", headers=VIEWER
                )

                local_incident = await _create_incident(client)
                invalid_cursor = await client.get(
                    f"/api/v1/incidents/{local_incident}/events",
                    headers={**VIEWER, "Last-Event-ID": f"{'9' * 20}-audit-overflow"},
                )

        assert missing_actor.status_code == 401
        assert cross_tenant.status_code == 404
        assert invalid_cursor.status_code == 400
        assert invalid_cursor.json()["code"] == "INVALID_LAST_EVENT_ID"
    finally:
        await migration_database.dispose()
