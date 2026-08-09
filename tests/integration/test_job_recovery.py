from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from incidentpilot.domain.alerts import AlertPayload
from incidentpilot.domain.enums import Severity
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.job_queue import PostgresJobQueue, SingleJobQueue
from incidentpilot.runtime.unit_of_work import UnitOfWork
from incidentpilot.worker.processor import JobProcessor
from incidentpilot.worker.recovery import recover_expired_jobs
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
WORKER_URL = "postgresql+asyncpg://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"
NOW = datetime(2026, 7, 16, 13, 0, tzinfo=UTC)


@pytest.mark.integration
async def test_job_claim_lease_retry_dead_letter_and_crash_recovery() -> None:
    migration_database = Database(MIGRATION_URL)
    api_database = Database(API_URL)
    worker_database = Database(WORKER_URL)
    incident_id = f"inc-job-{uuid4().hex}"
    job_id = f"job-{uuid4().hex}"
    try:
        await seed_local_data(migration_database)
        async with UnitOfWork(api_database) as uow:
            await uow.incidents.create_incident(
                incident_id=incident_id,
                tenant_id="local",
                alert=AlertPayload(
                    external_id=incident_id,
                    source="job-test",
                    title="Job recovery",
                    description="",
                    severity=Severity.P3,
                    starts_at=NOW,
                ),
            )
            await uow.commit()

        api_queue = PostgresJobQueue(api_database)
        worker_queue = PostgresJobQueue(worker_database)
        assert await api_queue.enqueue(
            job_id=job_id,
            incident_id=incident_id,
            job_type="START",
        )
        assert not await api_queue.enqueue(
            job_id=job_id,
            incident_id=incident_id,
            job_type="START",
        )

        claims = await asyncio.gather(
            worker_queue.claim("worker-1", lease_seconds=1, job_id=job_id),
            worker_queue.claim("worker-2", lease_seconds=1, job_id=job_id),
        )
        claimed = [job for job in claims if job is not None]
        assert len(claimed) == 1
        owner = "worker-1" if claims[0] is not None else "worker-2"
        assert await worker_queue.renew(job_id, owner, lease_seconds=1)

        await asyncio.sleep(1.1)
        assert await recover_expired_jobs(worker_queue) >= 1
        recovered = await worker_queue.claim("worker-recovery", lease_seconds=30, job_id=job_id)
        assert recovered is not None
        assert recovered.attempts == 2

        assert (
            await worker_queue.fail(
                job_id,
                "worker-recovery",
                max_attempts=3,
                base_delay_seconds=0,
            )
            == "retry"
        )
        final_attempt = await worker_queue.claim("worker-recovery", lease_seconds=30, job_id=job_id)
        assert final_attempt is not None
        assert final_attempt.attempts == 3
        assert (
            await worker_queue.fail(
                job_id,
                "worker-recovery",
                max_attempts=3,
                base_delay_seconds=0,
            )
            == "dead_letter"
        )
        dead_letter = await worker_queue.get(job_id)
        assert dead_letter is not None
        assert dead_letter.status == "dead_letter"

        processor_job_id = f"job-{uuid4().hex}"
        assert await api_queue.enqueue(
            job_id=processor_job_id,
            incident_id=incident_id,
            job_type="START",
        )
        handled: list[str] = []

        async def handler(job: object) -> None:
            handled.append(processor_job_id)

        processor = JobProcessor(
            queue=SingleJobQueue(worker_queue, processor_job_id),
            worker_id="worker-processor",
            handler=handler,
        )
        assert await processor.run_once()
        assert handled == [processor_job_id]
        completed = await worker_queue.get(processor_job_id)
        assert completed is not None
        assert completed.status == "completed"
    finally:
        await worker_database.dispose()
        await api_database.dispose()
        await migration_database.dispose()
