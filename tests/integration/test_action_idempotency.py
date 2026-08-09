from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from incidentpilot.domain.alerts import AlertPayload
from incidentpilot.domain.enums import Severity
from incidentpilot.incidents.models import ActionProposalRow
from incidentpilot.remediation.idempotency import SqlAlchemyExecutionIdempotencyStore
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.unit_of_work import UnitOfWork
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


@pytest.mark.integration
async def test_execution_idempotency_survives_a_fresh_database_session() -> None:
    migration_database = Database(MIGRATION_URL)
    api_database = Database(API_URL)
    restarted_database = Database(MIGRATION_URL)
    incident_id = f"inc-idempotency-{uuid4().hex}"
    proposal_id = f"proposal-{uuid4().hex}"
    idempotency_key = f"restart-checkout-{uuid4().hex}"
    first_execution_id = f"exec-{uuid4().hex}"
    repeated_execution_id = f"exec-{uuid4().hex}"
    try:
        await seed_local_data(migration_database)
        async with UnitOfWork(api_database) as uow:
            await uow.incidents.create_incident(
                incident_id=incident_id,
                tenant_id="local",
                alert=AlertPayload(
                    external_id=incident_id,
                    source="idempotency-test",
                    title="Idempotency test",
                    description="",
                    severity=Severity.P3,
                    starts_at=NOW,
                ),
            )
            await uow.commit()
        async with migration_database.session_factory() as session:
            session.add(
                ActionProposalRow(
                    id=proposal_id,
                    incident_id=incident_id,
                    payload_json={},
                    status="APPROVED",
                    policy_result_json={"allowed": True},
                )
            )
            await session.commit()
        async with migration_database.session_factory() as session:
            store = SqlAlchemyExecutionIdempotencyStore(session)
            first = await store.reserve(
                proposal_id=proposal_id,
                    idempotency_key=idempotency_key,
                execution_id=first_execution_id,
            )
            await store.complete(
                first_execution_id, status="succeeded", result={"target": "checkout"}
            )
            await session.commit()
        async with restarted_database.session_factory() as session:
            replay = await SqlAlchemyExecutionIdempotencyStore(session).reserve(
                proposal_id=proposal_id,
                idempotency_key=idempotency_key,
                execution_id=repeated_execution_id,
            )
            await session.commit()
        assert not first.replayed
        assert replay.replayed
        assert replay.execution_id == first_execution_id
        assert replay.status == "succeeded"
        assert replay.result == {"target": "checkout"}
    finally:
        await restarted_database.dispose()
        await api_database.dispose()
        await migration_database.dispose()
