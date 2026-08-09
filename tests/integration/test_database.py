from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from incidentpilot.domain.alerts import AlertPayload, TimeRange
from incidentpilot.domain.enums import EvidenceKind, IncidentStatus, Severity
from incidentpilot.domain.evidence import EvidenceRef
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.unit_of_work import UnitOfWork
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+psycopg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
TELEMETRY_URL = (
    "postgresql+asyncpg://telemetry_mcp_role:telemetry-local-only@127.0.0.1:5433/incidentpilot"
)
WORKER_URL = "postgresql+asyncpg://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"
NOW = datetime(2026, 7, 16, 11, 0, tzinfo=UTC)


def _alembic() -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", MIGRATION_URL)
    return config


@pytest.mark.integration
async def test_database_head_repository_permissions_and_idempotent_seed() -> None:
    config = _alembic()
    command.upgrade(config, "head")
    incident_id = f"inc-db-{uuid4().hex}"
    evidence_id = f"ev-db-{uuid4().hex}"

    migration_database = Database(MIGRATION_URL.replace("+psycopg", "+asyncpg"))
    api_database = Database(API_URL)
    telemetry_database = Database(TELEMETRY_URL)
    worker_database = Database(WORKER_URL)

    try:
        await seed_local_data(migration_database)
        await seed_local_data(migration_database)

        alert = AlertPayload(
            external_id=incident_id,
            source="integration-test",
            title="Checkout errors",
            description="Database integration test",
            severity=Severity.P1,
            starts_at=NOW,
            service_hint="checkout",
        )
        async with UnitOfWork(api_database) as uow:
            await uow.incidents.create_incident(
                incident_id=incident_id,
                tenant_id="local",
                alert=alert,
            )
            await uow.commit()

        evidence = EvidenceRef(
            id=evidence_id,
            incident_id=incident_id,
            kind=EvidenceKind.METRIC,
            source_system="prometheus",
            query={"template_id": "service_error_ratio"},
            observed_range=TimeRange(start=NOW, end=NOW),
            summary="Checkout error ratio is 100%",
            raw_digest_sha256="c" * 64,
            collected_at=NOW,
        )
        async with UnitOfWork(telemetry_database) as uow:
            await uow.incidents.add_evidence(evidence)
            await uow.commit()

        async with UnitOfWork(api_database) as uow:
            assert not hasattr(uow.incidents, "session")
            assert await uow.incidents.get_incident_status(incident_id) is IncidentStatus.RECEIVED
            assert await uow.incidents.get_evidence(evidence_id) == evidence

        async with migration_database.engine.connect() as connection:
            tenant_count = await connection.scalar(
                text("SELECT count(*) FROM tenants WHERE id = 'local'")
            )
            actor_count = await connection.scalar(
                text("SELECT count(*) FROM actors WHERE tenant_id = 'local'")
            )
        assert tenant_count == 1
        assert actor_count == 3

        with pytest.raises(DBAPIError, match="permission denied"):
            async with worker_database.engine.connect() as connection:
                await connection.execute(text("SELECT * FROM change_event_private_mappings"))
    finally:
        await api_database.dispose()
        await telemetry_database.dispose()
        await worker_database.dispose()
        await migration_database.dispose()
