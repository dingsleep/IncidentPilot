from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult

from incidentpilot.incidents.models import AnalysisJobRow
from incidentpilot.runtime.database import Database

type JobType = Literal["START", "RESUME"]
type JobStatus = Literal["queued", "running", "retry", "completed", "dead_letter"]


@dataclass(frozen=True)
class ClaimedJob:
    id: str
    incident_id: str
    job_type: JobType
    resume_reference_id: str | None
    attempts: int


@dataclass(frozen=True)
class JobRecord:
    id: str
    incident_id: str
    job_type: JobType
    resume_reference_id: str | None
    status: JobStatus
    attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    available_at: datetime


def retry_delay_seconds(
    attempts: int,
    *,
    base_delay_seconds: int = 5,
    max_delay_seconds: int = 300,
) -> int:
    if attempts < 1 or base_delay_seconds < 0 or max_delay_seconds < 0:
        raise ValueError("retry delay arguments must be non-negative and attempts at least one")
    return min(base_delay_seconds * 2 ** (attempts - 1), max_delay_seconds)


class PostgresJobQueue:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def enqueue(
        self,
        *,
        job_id: str,
        incident_id: str,
        job_type: JobType,
        resume_reference_id: str | None = None,
    ) -> bool:
        if (job_type == "START" and resume_reference_id is not None) or (
            job_type == "RESUME" and not resume_reference_id
        ):
            raise ValueError("START must not have a resume reference; RESUME must have one")
        async with self._database.session_factory() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    insert(AnalysisJobRow)
                    .values(
                        id=job_id,
                        incident_id=incident_id,
                        job_type=job_type,
                        resume_reference_id=resume_reference_id,
                        status="queued",
                        attempts=0,
                        available_at=func.clock_timestamp(),
                    )
                    .on_conflict_do_nothing(index_elements=[AnalysisJobRow.id])
                ),
            )
            return result.rowcount == 1

    async def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        job_id: str | None = None,
    ) -> ClaimedJob | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least one")
        async with self._database.session_factory() as session, session.begin():
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            query = (
                select(AnalysisJobRow)
                .where(
                    or_(
                        and_(
                            AnalysisJobRow.status.in_(("queued", "retry")),
                            AnalysisJobRow.available_at <= func.clock_timestamp(),
                        ),
                        and_(
                            AnalysisJobRow.status == "running",
                            AnalysisJobRow.lease_expires_at <= func.clock_timestamp(),
                        ),
                    )
                )
                .order_by(AnalysisJobRow.available_at, AnalysisJobRow.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job_id is not None:
                query = query.where(AnalysisJobRow.id == job_id)
            row = (await session.scalars(query)).first()
            if row is None:
                return None
            row.status = "running"
            row.lease_owner = worker_id
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            row.attempts += 1
            await session.flush()
            return ClaimedJob(
                id=row.id,
                incident_id=row.incident_id,
                job_type=cast(JobType, row.job_type),
                resume_reference_id=row.resume_reference_id,
                attempts=row.attempts,
            )

    async def renew(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
    ) -> bool:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least one")
        async with self._database.session_factory() as session, session.begin():
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(AnalysisJobRow)
                    .where(
                        AnalysisJobRow.id == job_id,
                        AnalysisJobRow.status == "running",
                        AnalysisJobRow.lease_owner == worker_id,
                        AnalysisJobRow.lease_expires_at > func.clock_timestamp(),
                    )
                    .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
                ),
            )
            return result.rowcount == 1

    async def complete(self, job_id: str, worker_id: str) -> bool:
        async with self._database.session_factory() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(AnalysisJobRow)
                    .where(
                        AnalysisJobRow.id == job_id,
                        AnalysisJobRow.status == "running",
                        AnalysisJobRow.lease_owner == worker_id,
                    )
                    .values(
                        status="completed",
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                ),
            )
            return result.rowcount == 1

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        *,
        max_attempts: int = 3,
        base_delay_seconds: int = 5,
    ) -> JobStatus:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        async with self._database.session_factory() as session, session.begin():
            row = (
                await session.scalars(
                    select(AnalysisJobRow).where(AnalysisJobRow.id == job_id).with_for_update()
                )
            ).one()
            if row.status != "running" or row.lease_owner != worker_id:
                raise RuntimeError("job is not leased by this worker")
            if row.attempts >= max_attempts:
                row.status = "dead_letter"
            else:
                now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
                row.status = "retry"
                row.available_at = now + timedelta(
                    seconds=retry_delay_seconds(
                        row.attempts,
                        base_delay_seconds=base_delay_seconds,
                    )
                )
            row.lease_owner = None
            row.lease_expires_at = None
            await session.flush()
            return cast(JobStatus, row.status)

    async def recover_expired(self) -> int:
        async with self._database.session_factory() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(AnalysisJobRow)
                    .where(
                        AnalysisJobRow.status == "running",
                        AnalysisJobRow.lease_expires_at <= func.clock_timestamp(),
                    )
                    .values(
                        status="retry",
                        lease_owner=None,
                        lease_expires_at=None,
                        available_at=func.clock_timestamp(),
                    )
                ),
            )
            return result.rowcount

    async def get(self, job_id: str) -> JobRecord | None:
        async with self._database.session_factory() as session:
            row = await session.get(AnalysisJobRow, job_id)
            return self._record(row) if row else None

    @staticmethod
    def _record(row: AnalysisJobRow) -> JobRecord:
        return JobRecord(
            id=row.id,
            incident_id=row.incident_id,
            job_type=cast(JobType, row.job_type),
            resume_reference_id=row.resume_reference_id,
            status=cast(JobStatus, row.status),
            attempts=row.attempts,
            lease_owner=row.lease_owner,
            lease_expires_at=row.lease_expires_at,
            available_at=row.available_at,
        )


class SingleJobQueue:
    """Restrict a processor to one known job without changing global FIFO behavior."""

    def __init__(self, queue: PostgresJobQueue, job_id: str) -> None:
        self._queue = queue
        self._job_id = job_id

    async def claim(self, worker_id: str, *, lease_seconds: int = 60) -> ClaimedJob | None:
        return await self._queue.claim(
            worker_id,
            lease_seconds=lease_seconds,
            job_id=self._job_id,
        )

    async def complete(self, job_id: str, worker_id: str) -> bool:
        return await self._queue.complete(job_id, worker_id)

    async def renew(self, job_id: str, worker_id: str, *, lease_seconds: int = 60) -> bool:
        return await self._queue.renew(job_id, worker_id, lease_seconds=lease_seconds)

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        *,
        max_attempts: int,
        base_delay_seconds: int,
    ) -> JobStatus:
        return await self._queue.fail(
            job_id,
            worker_id,
            max_attempts=max_attempts,
            base_delay_seconds=base_delay_seconds,
        )
