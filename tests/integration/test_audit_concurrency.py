from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from incidentpilot.domain.alerts import AlertPayload
from incidentpilot.domain.enums import Severity
from incidentpilot.incidents.timeline import verify_audit_chain
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.unit_of_work import UnitOfWork
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


@pytest.mark.integration
async def test_concurrent_audit_appends_form_one_transactionally_locked_chain() -> None:
    migration_database = Database(MIGRATION_URL)
    api_database = Database(API_URL)
    incident_id = f"inc-audit-{uuid4().hex}"
    try:
        await seed_local_data(migration_database)
        async with UnitOfWork(api_database) as uow:
            await uow.incidents.create_incident(
                incident_id=incident_id,
                tenant_id="local",
                alert=AlertPayload(
                    external_id=incident_id,
                    source="audit-test",
                    title="Audit concurrency",
                    description="",
                    severity=Severity.P3,
                    starts_at=NOW,
                ),
            )
            await uow.commit()

        async def append(event_type: str) -> None:
            async with UnitOfWork(api_database) as uow:
                await uow.timeline.append(
                    event_id=f"audit-{uuid4().hex}",
                    tenant_id="local",
                    incident_id=incident_id,
                    actor_type="worker",
                    actor_id=event_type,
                    event_type=event_type,
                    payload={"authorization": "Bearer secret", "event": event_type},
                )
                await uow.commit()

        await asyncio.gather(append("event.one"), append("event.two"))

        async with UnitOfWork(api_database) as uow:
            events = await uow.timeline.list_events(
                tenant_id="local",
                incident_id=incident_id,
            )

        assert len(events) == 2
        assert verify_audit_chain(events)
        assert events[0].prev_hash is None
        assert events[1].prev_hash == events[0].event_hash
        assert all(event.payload["authorization"] == "[REDACTED]" for event in events)
    finally:
        await api_database.dispose()
        await migration_database.dispose()
