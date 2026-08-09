from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from incidentpilot.domain.alerts import AlertPayload, TimeRange
from incidentpilot.domain.enums import EvidenceKind, Severity
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.unit_of_work import UnitOfWork
from incidentpilot.telemetry.evidence_store import EvidenceWrite
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
TELEMETRY_URL = (
    "postgresql+asyncpg://telemetry_mcp_role:telemetry-local-only@127.0.0.1:5433/incidentpilot"
)
NOW = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)


@pytest.mark.integration
async def test_database_evidence_store_redacts_and_deduplicates() -> None:
    migration_database = Database(MIGRATION_URL)
    api_database = Database(API_URL)
    telemetry_database = Database(TELEMETRY_URL)
    incident_id = f"inc-evidence-{uuid4().hex}"
    try:
        await seed_local_data(migration_database)
        async with UnitOfWork(api_database) as uow:
            await uow.incidents.create_incident(
                incident_id=incident_id,
                tenant_id="local",
                alert=AlertPayload(
                    external_id=incident_id,
                    source="evidence-test",
                    title="Evidence persistence",
                    description="",
                    severity=Severity.P3,
                    starts_at=NOW,
                ),
            )
            await uow.commit()

        def write(secret: str) -> EvidenceWrite:
            return EvidenceWrite(
                incident_id=incident_id,
                kind=EvidenceKind.METRIC,
                source_system="prometheus",
                query={"template_id": "service_error_ratio"},
                raw_json={"authorization": secret, "series": [1, 2]},
                observed_range=TimeRange(start=NOW, end=NOW),
                source_uri="http://127.0.0.1:3000/explore",
                truncated=False,
                collected_at=NOW,
            )

        async with UnitOfWork(telemetry_database) as uow:
            first = await uow.evidence.persist(write("Bearer first"))
            await uow.commit()
        async with UnitOfWork(telemetry_database) as uow:
            duplicate = await uow.evidence.persist(write("Bearer second"))
            await uow.commit()

        assert duplicate.id == first.id
        async with migration_database.engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT raw_json FROM evidence "
                        "WHERE incident_id = :incident_id AND kind = 'metric'"
                    ),
                    {"incident_id": incident_id},
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].raw_json["authorization"] == "[REDACTED]"

        async with UnitOfWork(api_database) as uow:
            assert await uow.incidents.get_evidence(first.id) == first
    finally:
        await telemetry_database.dispose()
        await api_database.dispose()
        await migration_database.dispose()
