from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Protocol

from incidentpilot.runtime.job_queue import ClaimedJob, JobStatus

logger = logging.getLogger(__name__)


class JobQueuePort(Protocol):
    async def claim(self, worker_id: str, *, lease_seconds: int = 60) -> ClaimedJob | None: ...

    async def renew(self, job_id: str, worker_id: str, *, lease_seconds: int = 60) -> bool: ...

    async def complete(self, job_id: str, worker_id: str) -> bool: ...

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        *,
        max_attempts: int,
        base_delay_seconds: int,
    ) -> JobStatus: ...


class JobProcessor:
    def __init__(
        self,
        *,
        queue: JobQueuePort,
        worker_id: str,
        handler: Callable[[ClaimedJob], Awaitable[None]],
        max_attempts: int = 3,
        base_delay_seconds: int = 5,
        lease_seconds: int = 60,
        renew_interval_seconds: float = 20,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least one")
        if renew_interval_seconds <= 0 or renew_interval_seconds >= lease_seconds:
            raise ValueError("renew interval must be positive and shorter than the lease")
        self._queue = queue
        self._worker_id = worker_id
        self._handler = handler
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._lease_seconds = lease_seconds
        self._renew_interval_seconds = renew_interval_seconds

    async def run_once(self) -> bool:
        job = await self._queue.claim(self._worker_id, lease_seconds=self._lease_seconds)
        if job is None:
            return False
        renewer = asyncio.create_task(self._renew_lease(job))
        try:
            await self._await_handler(job, renewer)
        except Exception:
            logger.exception("analysis job failed: %s", job.id)
            await self._queue.fail(
                job.id,
                self._worker_id,
                max_attempts=self._max_attempts,
                base_delay_seconds=self._base_delay_seconds,
            )
        else:
            completed = await self._queue.complete(job.id, self._worker_id)
            if not completed:
                raise RuntimeError("job lease was lost before completion")
        finally:
            renewer.cancel()
            with suppress(asyncio.CancelledError):
                await renewer
        return True

    async def _renew_lease(self, job: ClaimedJob) -> None:
        while True:
            await asyncio.sleep(self._renew_interval_seconds)
            renewed = await self._queue.renew(
                job.id,
                self._worker_id,
                lease_seconds=self._lease_seconds,
            )
            if not renewed:
                raise RuntimeError(f"job lease was lost while handling {job.id}")

    async def _await_handler(self, job: ClaimedJob, renewer: asyncio.Task[None]) -> None:
        handler = asyncio.create_task(self._run_handler(job))
        try:
            done, _ = await asyncio.wait({handler, renewer}, return_when=asyncio.FIRST_COMPLETED)
            if renewer in done:
                renewer.result()
            await handler
        except Exception:
            handler.cancel()
            with suppress(asyncio.CancelledError):
                await handler
            raise

    async def _run_handler(self, job: ClaimedJob) -> None:
        await self._handler(job)
