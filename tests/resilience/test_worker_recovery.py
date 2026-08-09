from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from incidentpilot.domain.alerts import AlertPayload
from incidentpilot.domain.enums import Severity
from incidentpilot.evaluation.isolation import FlagdScenarioController
from incidentpilot.evaluation.loader import load_episode_suite
from incidentpilot.evaluation.runner import EnvironmentMetadata, EpisodeRunner, HealthSnapshot
from incidentpilot.incidents.models import ActionExecutionRow, ActionProposalRow, AnalysisJobRow
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.job_queue import ClaimedJob, JobStatus, PostgresJobQueue, SingleJobQueue
from incidentpilot.runtime.unit_of_work import UnitOfWork
from incidentpilot.worker.main import run_worker
from incidentpilot.worker.processor import JobProcessor
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+psycopg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
WORKER_URL = "postgresql+asyncpg://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"
ROOT = Path(__file__).parents[2]


class _CrashRecoveringQueue:
    def __init__(self) -> None:
        self.job = ClaimedJob("job-resilience", "inc-resilience", "START", None, 1)
        self.status = "queued"

    async def claim(self, worker_id: str, *, lease_seconds: int = 60) -> ClaimedJob | None:
        del worker_id, lease_seconds
        if self.status != "queued":
            return None
        self.status = "running"
        return self.job

    async def complete(self, job_id: str, worker_id: str) -> bool:
        del job_id, worker_id
        self.status = "completed"
        return True

    async def renew(self, job_id: str, worker_id: str, *, lease_seconds: int = 60) -> bool:
        del job_id, worker_id, lease_seconds
        return self.status == "running"

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        *,
        max_attempts: int,
        base_delay_seconds: int,
    ) -> JobStatus:
        del job_id, worker_id, max_attempts, base_delay_seconds
        self.status = "retry"
        return self.status


@pytest.mark.asyncio
async def test_worker_crash_releases_the_lease_for_retry() -> None:
    queue = _CrashRecoveringQueue()

    async def crash(_: ClaimedJob) -> None:
        raise RuntimeError("worker killed")

    processor = JobProcessor(
        queue=queue,
        worker_id="worker-1",
        handler=crash,
        base_delay_seconds=0,
    )
    assert await processor.run_once()
    assert queue.status == "retry"


@pytest.mark.asyncio
async def test_worker_retries_after_a_transient_queue_failure() -> None:
    stop = asyncio.Event()

    class Queue:
        def __init__(self) -> None:
            self.calls = 0

        async def claim(self, worker_id: str, *, lease_seconds: int = 60) -> ClaimedJob | None:
            del worker_id, lease_seconds
            self.calls += 1
            if self.calls == 1:
                raise SQLAlchemyError("database temporarily unavailable")
            stop.set()
            return None

        async def complete(self, job_id: str, worker_id: str) -> bool:
            del job_id, worker_id
            return True

        async def renew(self, job_id: str, worker_id: str, *, lease_seconds: int = 60) -> bool:
            del job_id, worker_id, lease_seconds
            return True

        async def fail(
            self,
            job_id: str,
            worker_id: str,
            *,
            max_attempts: int,
            base_delay_seconds: int,
        ) -> JobStatus:
            del job_id, worker_id, max_attempts, base_delay_seconds
            return "retry"

    queue = Queue()
    async def handler(_: ClaimedJob) -> None:
        raise AssertionError("the recovered queue did not claim a job")

    processor = JobProcessor(queue=queue, worker_id="worker-db-retry", handler=handler)
    await run_worker(processor, stop=stop, idle_seconds=0.001)

    assert queue.calls == 2


@pytest.mark.integration
async def test_twenty_repeated_jobs_leave_no_active_jobs_or_actions() -> None:
    migration_database = Database(MIGRATION_URL.replace("+psycopg", "+asyncpg"))
    api_database = Database(API_URL)
    worker_database = Database(WORKER_URL)
    queue = PostgresJobQueue(worker_database)
    incident_id = f"inc-resilience-{uuid4().hex}"
    completed: list[str] = []
    try:
        await seed_local_data(migration_database)
        async with UnitOfWork(api_database) as uow:
            await uow.incidents.create_incident(
                incident_id=incident_id,
                tenant_id="local",
                alert=AlertPayload(
                    external_id=incident_id,
                    source="resilience-test",
                    title="Repeated job resilience",
                    description="",
                    severity=Severity.P3,
                    starts_at=datetime.now(UTC),
                ),
            )
            await uow.commit()
        for index in range(20):
            job_id = f"job-resilience-{uuid4().hex}"
            assert await queue.enqueue(job_id=job_id, incident_id=incident_id, job_type="START")

            async def handler(job: ClaimedJob) -> None:
                completed.append(job.id)

            assert await JobProcessor(
                queue=SingleJobQueue(queue, job_id),
                worker_id=f"worker-resilience-{index}",
                handler=handler,
                base_delay_seconds=0,
            ).run_once()

        async with worker_database.session_factory() as session:
            active_jobs = await session.scalar(
                select(func.count())
                .select_from(AnalysisJobRow)
                .where(
                    AnalysisJobRow.incident_id == incident_id,
                    AnalysisJobRow.status.in_(("queued", "running", "retry")),
                )
            )
        async with migration_database.session_factory() as session:
            action_count = await session.scalar(
                select(func.count())
                .select_from(ActionExecutionRow)
                .join(ActionProposalRow, ActionExecutionRow.proposal_id == ActionProposalRow.id)
                .where(ActionProposalRow.incident_id == incident_id)
            )
        assert len(completed) == 20
        assert active_jobs == 0
        assert action_count == 0
    finally:
        await worker_database.dispose()
        await api_database.dispose()
        await migration_database.dispose()


@pytest.mark.integration
def test_twenty_fault_activations_restore_the_real_flagd_snapshot() -> None:
    episode = next(
        item
        for item in load_episode_suite(
            ROOT / "scenarios", ROOT / "service_catalog" / "otel-demo.yaml"
        )
        if item.id == "payment-failure-001"
    )
    client = httpx.Client(timeout=5, trust_env=False)
    controller = FlagdScenarioController(client=client, base_url="http://127.0.0.1:4000/api")
    original = controller.snapshot()
    runner = EpisodeRunner(
        controller=controller,
        preflight=lambda: EnvironmentMetadata(
            demo_tag="2.2.0",
            demo_commit="b74a7bc7bbe66099c61951f42b24dab8b6f02d18",
            prompt_version="v1",
            model_profile="resilience",
            tool_version="telemetry-v9",
        ),
        capture_health=lambda: HealthSnapshot(
            healthy=True,
            details={"paymentFailure": controller.read_config()["flags"]["paymentFailure"]},
        ),
        send_alert=lambda _public: "alert-resilience",
        drive_traffic=lambda _traffic: None,
        run_agent=lambda _public, _seed: {},
        score=lambda _output, _execution: {},
        sleep=lambda _seconds: None,
    )
    try:
        results = [runner.run(episode, seed=index) for index in range(20)]
        assert all(result.recovery.healthy for result in results)
        assert controller.snapshot().digest == original.digest
    finally:
        client.close()
