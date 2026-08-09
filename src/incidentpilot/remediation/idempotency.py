from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from incidentpilot.domain import DomainModel
from incidentpilot.incidents.models import ActionExecutionRow

ExecutionStatus = Literal["pending", "succeeded", "failed"]


class ExecutionReservation(DomainModel):
    execution_id: str
    proposal_id: str
    status: ExecutionStatus
    result: dict[str, Any]
    replayed: bool


class InMemoryExecutionIdempotencyStore:
    """Unit-test equivalent of the action_executions unique key contract."""

    def __init__(self) -> None:
        self._records: dict[str, ExecutionReservation] = {}

    def reserve(
        self, *, proposal_id: str, idempotency_key: str, execution_id: str
    ) -> ExecutionReservation:
        existing = self._records.get(idempotency_key)
        if existing is not None:
            return existing.model_copy(update={"replayed": True})
        reservation = ExecutionReservation(
            execution_id=execution_id,
            proposal_id=proposal_id,
            status="pending",
            result={},
            replayed=False,
        )
        self._records[idempotency_key] = reservation
        return reservation

    def complete(
        self, execution_id: str, *, status: ExecutionStatus, result: dict[str, Any]
    ) -> ExecutionReservation:
        for key, record in self._records.items():
            if record.execution_id == execution_id:
                completed = record.model_copy(update={"status": status, "result": result})
                self._records[key] = completed
                return completed
        raise KeyError(f"unknown execution: {execution_id}")


class SqlAlchemyExecutionIdempotencyStore:
    """Persist reservations through the existing unique idempotency constraint."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(
        self, *, proposal_id: str, idempotency_key: str, execution_id: str
    ) -> ExecutionReservation:
        try:
            async with self._session.begin_nested():
                row = ActionExecutionRow(
                    id=execution_id,
                    proposal_id=proposal_id,
                    idempotency_key=idempotency_key,
                    status="pending",
                    started_at=datetime.now(UTC),
                    result_json={},
                )
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            row = await self._session.scalar(
                select(ActionExecutionRow).where(
                    ActionExecutionRow.idempotency_key == idempotency_key
                )
            )
            if row is None:
                raise
            return _reservation(row, replayed=True)
        return _reservation(row, replayed=False)

    async def complete(
        self, execution_id: str, *, status: ExecutionStatus, result: dict[str, Any]
    ) -> ExecutionReservation:
        row = await self._session.get(ActionExecutionRow, execution_id)
        if row is None:
            raise KeyError(f"unknown execution: {execution_id}")
        row.status = status
        row.result_json = result
        row.finished_at = datetime.now(UTC)
        await self._session.flush()
        return _reservation(row, replayed=False)


def _reservation(row: ActionExecutionRow, *, replayed: bool) -> ExecutionReservation:
    return ExecutionReservation(
        execution_id=row.id,
        proposal_id=row.proposal_id,
        status=cast(ExecutionStatus, row.status),
        result=row.result_json,
        replayed=replayed,
    )
