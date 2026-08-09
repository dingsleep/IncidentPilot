from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from incidentpilot.domain.alerts import AlertPayload
from incidentpilot.domain.enums import Severity
from incidentpilot.llm.usage import (
    ModelCallRecord,
    ModelUsage,
    SqlAlchemyModelCallRecorder,
)
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.unit_of_work import UnitOfWork
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
WORKER_URL = "postgresql+asyncpg://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"


@pytest.mark.integration
async def test_worker_records_each_model_attempt_without_prompt_content() -> None:
    migration_database = Database(MIGRATION_URL)
    api_database = Database(API_URL)
    worker_database = Database(WORKER_URL)
    incident_id = f"inc-model-{uuid4().hex}"
    call_id = f"mc_{uuid4().hex}"
    try:
        await seed_local_data(migration_database)
        async with UnitOfWork(api_database) as uow:
            await uow.incidents.create_incident(
                incident_id=incident_id,
                tenant_id="local",
                alert=AlertPayload(
                    external_id=incident_id,
                    source="model-call-test",
                    title="Model call recorder",
                    description="",
                    severity=Severity.P3,
                    starts_at=datetime.now(UTC),
                ),
            )
            await uow.commit()

        await SqlAlchemyModelCallRecorder(worker_database).record(
            ModelCallRecord(
                call_id=call_id,
                incident_id=incident_id,
                agent_name="triage",
                model_profile="fast",
                prompt_version="v1",
                strategy="tool_strategy",
                attempt=1,
                status="SUCCESS",
                structured_response={"severity": "P3"},
                usage=ModelUsage(
                    input_tokens=20,
                    output_tokens=5,
                    cost_microusd=3,
                ),
                latency_ms=120,
            )
        )

        async with migration_database.engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT model_profile, prompt_version, input_tokens, output_tokens, "
                        "cost_microusd, duration_ms, status "
                        "FROM model_calls WHERE id = :call_id"
                    ),
                    {"call_id": call_id},
                )
            ).one()
        assert tuple(row) == ("fast", "v1", 20, 5, 3, 120, "SUCCESS")
    finally:
        await worker_database.dispose()
        await api_database.dispose()
        await migration_database.dispose()
