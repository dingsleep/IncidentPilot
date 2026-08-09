from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from incidentpilot.domain import DomainModel
from incidentpilot.remediation.adapters.docker import DockerRestartAdapter, DockerRestartReceipt
from incidentpilot.remediation.adapters.flagd import (
    FlagdChangeMapping,
    FlagdRollbackAdapter,
    FlagdRollbackReceipt,
)


class SanitizedExecutionOutput(DomainModel):
    """The complete Action-facing result; never includes adapter payloads or snapshots."""

    execution_id: str
    status: Literal["succeeded", "failed"]
    target_service: str
    started_at: datetime
    finished_at: datetime
    reference: str | None = None


class ActionExecutor:
    """Select only a typed, bounded adapter and return a sanitized result."""

    def __init__(
        self,
        *,
        docker: DockerRestartAdapter | None,
        flagd: FlagdRollbackAdapter | None,
    ) -> None:
        self._docker = docker
        self._flagd = flagd

    def restart_service(
        self,
        *,
        execution_id: str,
        target_service: str,
        grace_period_seconds: int,
    ) -> SanitizedExecutionOutput:
        docker = self._docker
        if docker is None:
            raise RuntimeError("docker restart adapter is not configured")
        return self._execute(
            execution_id=execution_id,
            target_service=target_service,
            operation=lambda: docker.restart(
                target_service=target_service,
                grace_period_seconds=grace_period_seconds,
            ),
        )

    def rollback_change(
        self,
        *,
        execution_id: str,
        change_id: str,
        target_service: str,
    ) -> SanitizedExecutionOutput:
        flagd = self._flagd
        if flagd is None:
            raise RuntimeError("flagd rollback adapter is not configured")
        return self._execute(
            execution_id=execution_id,
            target_service=target_service,
            operation=lambda: flagd.rollback(
                change_id=change_id,
                target_service=target_service,
            ),
        )

    def rollback_change_with_mapping(
        self,
        *,
        execution_id: str,
        mapping: FlagdChangeMapping,
        target_service: str,
    ) -> SanitizedExecutionOutput:
        flagd = self._flagd
        if flagd is None:
            raise RuntimeError("flagd rollback adapter is not configured")
        return self._execute(
            execution_id=execution_id,
            target_service=target_service,
            operation=lambda: flagd.rollback_mapping(
                mapping=mapping,
                target_service=target_service,
            ),
        )

    @staticmethod
    def _execute(
        *,
        execution_id: str,
        target_service: str,
        operation: Callable[[], DockerRestartReceipt | FlagdRollbackReceipt],
    ) -> SanitizedExecutionOutput:
        started_at = datetime.now(UTC)
        try:
            receipt = operation()
        except Exception:
            return SanitizedExecutionOutput(
                execution_id=execution_id,
                status="failed",
                target_service=target_service,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        return SanitizedExecutionOutput(
            execution_id=execution_id,
            status="succeeded",
            target_service=receipt.target_service,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            reference=receipt.reference,
        )
