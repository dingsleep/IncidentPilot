from __future__ import annotations

import asyncio

import pytest

from incidentpilot.runtime.job_queue import ClaimedJob, JobStatus, retry_delay_seconds
from incidentpilot.worker.processor import JobProcessor


class FakeQueue:
    def __init__(self, job: ClaimedJob, *, fail_handler: bool = False) -> None:
        self.job = job
        self.fail_handler = fail_handler
        self.completed: list[str] = []
        self.failed: list[str] = []
        self.renewed: list[str] = []
        self.renewed_event = asyncio.Event()
        self.claimed = False

    async def claim(self, worker_id: str, *, lease_seconds: int = 60) -> ClaimedJob | None:
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    async def complete(self, job_id: str, worker_id: str) -> bool:
        self.completed.append(job_id)
        return True

    async def renew(self, job_id: str, worker_id: str, *, lease_seconds: int = 60) -> bool:
        self.renewed.append(job_id)
        self.renewed_event.set()
        return True

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        *,
        max_attempts: int,
        base_delay_seconds: int,
    ) -> JobStatus:
        self.failed.append(job_id)
        return "retry"


def _job() -> ClaimedJob:
    return ClaimedJob(
        id="job-1",
        incident_id="inc-1",
        job_type="START",
        resume_reference_id=None,
        attempts=1,
    )


def test_retry_delay_is_exponential_and_capped() -> None:
    assert retry_delay_seconds(1, base_delay_seconds=5, max_delay_seconds=300) == 5
    assert retry_delay_seconds(2, base_delay_seconds=5, max_delay_seconds=300) == 10
    assert retry_delay_seconds(20, base_delay_seconds=5, max_delay_seconds=300) == 300


@pytest.mark.asyncio
async def test_processor_uses_test_handler_and_records_success_or_failure() -> None:
    handled: list[str] = []

    async def success(job: ClaimedJob) -> None:
        handled.append(job.id)

    success_queue = FakeQueue(_job())
    processor = JobProcessor(
        queue=success_queue,
        worker_id="worker-1",
        handler=success,
    )
    assert await processor.run_once()
    assert handled == ["job-1"]
    assert success_queue.completed == ["job-1"]

    async def failure(job: ClaimedJob) -> None:
        raise RuntimeError(job.id)

    failure_queue = FakeQueue(_job())
    processor = JobProcessor(
        queue=failure_queue,
        worker_id="worker-2",
        handler=failure,
    )
    assert await processor.run_once()
    assert failure_queue.failed == ["job-1"]


@pytest.mark.asyncio
async def test_processor_renews_a_lease_while_a_long_handler_runs() -> None:
    queue = FakeQueue(_job())

    async def long_running(_: ClaimedJob) -> None:
        await queue.renewed_event.wait()

    processor = JobProcessor(
        queue=queue,
        worker_id="worker-lease",
        handler=long_running,
        lease_seconds=2,
        renew_interval_seconds=0.01,
    )

    assert await processor.run_once()
    assert queue.renewed_event.is_set()
    assert queue.completed == ["job-1"]
