from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from incidentpilot.domain.actions import VerificationCheck
from incidentpilot.domain.enums import EvidenceKind, IncidentStatus
from incidentpilot.incidents.models import EvidenceRow, IncidentRow
from incidentpilot.remediation.verification import SqlAlchemyVerificationEvidenceRecorder
from incidentpilot.runtime.database import Database
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)


@pytest.mark.integration
async def test_verification_reading_is_persisted_as_bounded_metric_evidence() -> None:
    database = Database(MIGRATION_URL)
    incident_id = f"inc-verification-{uuid4().hex}"
    observed_at = datetime.now(UTC)
    try:
        await seed_local_data(database)
        async with database.session_factory() as session, session.begin():
            session.add(
                IncidentRow(
                    id=incident_id,
                    tenant_id="local",
                    source="verification-test",
                    external_id=incident_id,
                    status=IncidentStatus.VERIFYING.value,
                    severity="P1",
                    title="Verification evidence test",
                )
            )

        evidence_id = await SqlAlchemyVerificationEvidenceRecorder(database=database).record(
            incident_id=incident_id,
            check=VerificationCheck(
                service="payment",
                metric="error_ratio",
                query_template_id="service_error_ratio",
                comparator="lt",
                threshold=0.05,
                observation_seconds=30,
            ),
            value=0.01,
            observed_at=observed_at,
        )

        async with database.session_factory() as session:
            evidence = await session.get(EvidenceRow, evidence_id)
        assert evidence is not None
        assert evidence.incident_id == incident_id
        assert evidence.kind == EvidenceKind.METRIC.value
        assert evidence.query_json == {
            "template_id": "service_error_ratio",
            "service": "payment",
            "metric": "error_ratio",
            "observation_seconds": 30,
        }
        assert evidence.raw_json == {"value": 0.01}
    finally:
        await database.dispose()
