from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from incidentpilot.auth.tokens import ApprovalGrant
from incidentpilot.domain.actions import ActionProposal, RestartServiceAction, RollbackChangeAction
from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.incidents.models import (
    ActionExecutionRow,
    ActionProposalRow,
    ApprovalRow,
    IncidentRow,
)
from incidentpilot.mcp_servers.common.auth import CallerContext
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope, ToolError
from incidentpilot.remediation.adapters.flagd import FlagdChangeMapping
from incidentpilot.remediation.executor import ActionExecutor, SanitizedExecutionOutput
from incidentpilot.remediation.idempotency import SqlAlchemyExecutionIdempotencyStore
from incidentpilot.remediation.private_mappings import SqlAlchemyPrivateMappingRepository
from incidentpilot.runtime.database import Database
from incidentpilot.telemetry.normalization import canonical_digest


class ActionCallerContext(CallerContext):
    approval_grant: ApprovalGrant
    grant_digest: str


class ActionStore(Protocol):
    async def list_allowed(self, caller: CallerContext, *, target_service: str) -> ToolEnvelope: ...

    async def restart(
        self,
        caller: ActionCallerContext,
        *,
        proposal_id: str,
        target_service: str,
        idempotency_key: str,
    ) -> ToolEnvelope: ...

    async def rollback(
        self,
        caller: ActionCallerContext,
        *,
        proposal_id: str,
        change_id: str,
        idempotency_key: str,
    ) -> ToolEnvelope: ...

    async def status(self, caller: CallerContext, *, execution_id: str) -> ToolEnvelope: ...


class ActionToolHandlers:
    def __init__(self, *, store: ActionStore) -> None:
        self._store = store

    async def list_allowed_actions(
        self, caller: CallerContext, *, target_service: str
    ) -> ToolEnvelope:
        try:
            return await self._store.list_allowed(caller, target_service=target_service)
        except TimeoutError:
            return _timeout()

    async def restart_service(
        self,
        caller: ActionCallerContext,
        *,
        proposal_id: str,
        target_service: str,
        idempotency_key: str,
    ) -> ToolEnvelope:
        try:
            return await self._store.restart(
                caller,
                proposal_id=proposal_id,
                target_service=target_service,
                idempotency_key=idempotency_key,
            )
        except TimeoutError:
            return _timeout()

    async def rollback_change(
        self,
        caller: ActionCallerContext,
        *,
        proposal_id: str,
        change_id: str,
        idempotency_key: str,
    ) -> ToolEnvelope:
        try:
            return await self._store.rollback(
                caller,
                proposal_id=proposal_id,
                change_id=change_id,
                idempotency_key=idempotency_key,
            )
        except TimeoutError:
            return _timeout()

    async def get_action_status(self, caller: CallerContext, *, execution_id: str) -> ToolEnvelope:
        return await self._store.status(caller, execution_id=execution_id)


class InMemoryActionStore:
    """Contract-test double; production server-side revalidation is added next."""

    def __init__(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        proposal_id: str,
        proposal_payload_digest: str,
    ) -> None:
        self._tenant_id = tenant_id
        self._incident_id = incident_id
        self._proposal_id = proposal_id
        self._proposal_payload_digest = proposal_payload_digest

    @classmethod
    def for_contract_test(
        cls,
        *,
        tenant_id: str,
        incident_id: str,
        proposal_id: str,
        proposal_payload_digest: str,
    ) -> InMemoryActionStore:
        return cls(
            tenant_id=tenant_id,
            incident_id=incident_id,
            proposal_id=proposal_id,
            proposal_payload_digest=proposal_payload_digest,
        )

    async def list_allowed(self, caller: CallerContext, *, target_service: str) -> ToolEnvelope:
        if not self._owns(caller):
            return _forbidden("incident ownership mismatch")
        return _success(
            {
                "target_service": target_service,
                "actions": ["restart_service", "rollback_change"],
            }
        )

    async def restart(
        self,
        caller: ActionCallerContext,
        *,
        proposal_id: str,
        target_service: str,
        idempotency_key: str,
    ) -> ToolEnvelope:
        del target_service, idempotency_key
        return self._write_result(caller, proposal_id, "restart_service")

    async def rollback(
        self,
        caller: ActionCallerContext,
        *,
        proposal_id: str,
        change_id: str,
        idempotency_key: str,
    ) -> ToolEnvelope:
        del change_id, idempotency_key
        return self._write_result(caller, proposal_id, "rollback_change")

    async def status(self, caller: CallerContext, *, execution_id: str) -> ToolEnvelope:
        del caller
        return ToolEnvelope(
            ok=True,
            tool_call_id=f"tc_{uuid4().hex}",
            data={"execution_id": execution_id, "status": "not_found"},
        )

    def _write_result(
        self, caller: ActionCallerContext, proposal_id: str, action_type: str
    ) -> ToolEnvelope:
        if not self._owns(caller) or proposal_id != self._proposal_id:
            return _forbidden("proposal ownership mismatch")
        return _success({"status": "accepted", "action_type": action_type})

    def _owns(self, caller: CallerContext) -> bool:
        return caller.tenant_id == self._tenant_id and caller.incident_id == self._incident_id


class SqlAlchemyActionStore:
    """Re-read approval state and atomically consume the grant nonce before a bounded action."""

    def __init__(
        self,
        *,
        database: Database,
        executor: ActionExecutor,
        private_mappings: SqlAlchemyPrivateMappingRepository | None = None,
        allowed_actions: frozenset[str] = frozenset({"restart_service", "rollback_change"}),
    ) -> None:
        if not allowed_actions <= {"restart_service", "rollback_change"}:
            raise ValueError("allowed actions must be bounded Action MCP operations")
        self._database = database
        self._executor = executor
        self._private_mappings = private_mappings
        self._allowed_actions = allowed_actions

    async def list_allowed(self, caller: CallerContext, *, target_service: str) -> ToolEnvelope:
        async with self._database.session_factory() as session:
            incident = await session.get(IncidentRow, caller.incident_id)
        if incident is None or incident.tenant_id != caller.tenant_id:
            return _forbidden("incident ownership mismatch")
        if incident.status not in {IncidentStatus.DIAGNOSED.value, IncidentStatus.PLANNING.value}:
            return _forbidden("incident is not eligible for action planning")
        return _success(
            {
                "target_service": target_service,
                "actions": sorted(self._allowed_actions),
            }
        )

    async def restart(
        self,
        caller: ActionCallerContext,
        *,
        proposal_id: str,
        target_service: str,
        idempotency_key: str,
    ) -> ToolEnvelope:
        return await self._execute(
            caller=caller,
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            action_type="restart_service",
            target_service=target_service,
            change_id=None,
        )

    async def rollback(
        self,
        caller: ActionCallerContext,
        *,
        proposal_id: str,
        change_id: str,
        idempotency_key: str,
    ) -> ToolEnvelope:
        return await self._execute(
            caller=caller,
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            action_type="rollback_change",
            target_service=None,
            change_id=change_id,
        )

    async def status(self, caller: CallerContext, *, execution_id: str) -> ToolEnvelope:
        async with self._database.session_factory() as session:
            execution = await session.scalar(
                select(ActionExecutionRow)
                .join(ActionProposalRow, ActionProposalRow.id == ActionExecutionRow.proposal_id)
                .join(IncidentRow, IncidentRow.id == ActionProposalRow.incident_id)
                .where(
                    ActionExecutionRow.id == execution_id,
                    IncidentRow.id == caller.incident_id,
                    IncidentRow.tenant_id == caller.tenant_id,
                )
            )
        if execution is None:
            return ToolEnvelope(
                ok=False,
                tool_call_id=f"tc_{uuid4().hex}",
                error=ToolError(
                    code="NOT_FOUND",
                    message="action execution was not found",
                    retryable=False,
                ),
            )
        return _success(
            {
                "execution_id": execution.id,
                "status": execution.status,
                "result": execution.result_json,
            }
        )

    async def _execute(
        self,
        *,
        caller: ActionCallerContext,
        proposal_id: str,
        idempotency_key: str,
        action_type: str,
        target_service: str | None,
        change_id: str | None,
    ) -> ToolEnvelope:
        async with self._database.session_factory() as session, session.begin():
            proposal = await self._authorize(session, caller=caller, proposal_id=proposal_id)
            if isinstance(proposal, ToolEnvelope):
                return proposal
            action = proposal.action
            if action_type not in self._allowed_actions:
                return _forbidden("action type is disabled by the Action Controller")
            if isinstance(action, RestartServiceAction):
                if action_type != action.action_type or target_service != action.target_service:
                    return _forbidden("stored proposal does not match restart request")
            elif action_type != action.action_type or change_id != action.change_id:
                return _forbidden("stored proposal does not match rollback request")
            idempotency = SqlAlchemyExecutionIdempotencyStore(session)
            existing = await session.scalar(
                select(ActionExecutionRow).where(
                    ActionExecutionRow.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                return _success(
                    {
                        "execution_id": existing.id,
                        "status": "already_applied",
                        "result": existing.result_json,
                    }
                )
            private_mapping = None
            if isinstance(action, RollbackChangeAction) and self._private_mappings is not None:
                private_mapping = await self._private_mappings.get(action.change_id)
                if private_mapping is None:
                    return _forbidden("private rollback mapping was not found")
            consumed = await session.scalar(
                update(ApprovalRow)
                .where(
                    ApprovalRow.proposal_id == proposal_id,
                    ApprovalRow.grant_digest == caller.grant_digest,
                    ApprovalRow.nonce_used_at.is_(None),
                )
                .values(nonce_used_at=datetime.now(UTC))
                .returning(ApprovalRow.id)
            )
            if consumed is None:
                return _forbidden("approval nonce was already consumed")
            reservation = await idempotency.reserve(
                proposal_id=proposal_id,
                idempotency_key=idempotency_key,
                execution_id=f"exec_{uuid4().hex}",
            )
            if reservation.replayed:
                return _success(
                    {
                        "execution_id": reservation.execution_id,
                        "status": "already_applied",
                        "result": reservation.result,
                    }
                )
            output = self._run_executor(
                execution_id=reservation.execution_id,
                action=action,
                private_mapping=private_mapping,
            )
            completed = await idempotency.complete(
                reservation.execution_id,
                status="succeeded" if output.status == "succeeded" else "failed",
                result=output.model_dump(mode="json"),
            )
        return _success(completed.result)

    async def _authorize(
        self, session: AsyncSession, *, caller: ActionCallerContext, proposal_id: str
    ) -> ActionProposal | ToolEnvelope:
        incident = await session.get(IncidentRow, caller.incident_id)
        proposal_row = await session.get(ActionProposalRow, proposal_id)
        approval = await session.scalar(
            select(ApprovalRow).where(
                ApprovalRow.proposal_id == proposal_id,
                ApprovalRow.grant_digest == caller.grant_digest,
            )
        )
        if (
            incident is None
            or incident.tenant_id != caller.tenant_id
            or proposal_row is None
            or proposal_row.incident_id != caller.incident_id
            or approval is None
            or approval.actor_id != caller.approval_grant.actor_id
            or approval.decision.upper() != "APPROVED"
            or proposal_row.status.upper() != "APPROVED"
            or not bool(proposal_row.policy_result_json.get("allowed"))
            or incident.status != IncidentStatus.AUTHORIZING.value
            or caller.approval_grant.proposal_id != proposal_id
            or caller.approval_grant.incident_id != caller.incident_id
            or caller.approval_grant.tenant_id != caller.tenant_id
            or canonical_digest(proposal_row.payload_json)
            != caller.approval_grant.proposal_payload_digest
        ):
            return _forbidden("approval authorization no longer matches server state")
        try:
            return ActionProposal.model_validate(proposal_row.payload_json)
        except ValueError:
            return _forbidden("stored proposal payload is invalid")

    def _run_executor(
        self,
        *,
        execution_id: str,
        action: RestartServiceAction | RollbackChangeAction,
        private_mapping: FlagdChangeMapping | None,
    ) -> SanitizedExecutionOutput:
        if isinstance(action, RestartServiceAction):
            return self._executor.restart_service(
                execution_id=execution_id,
                target_service=action.target_service,
                grace_period_seconds=action.grace_period_seconds,
            )
        if private_mapping is not None:
            return self._executor.rollback_change_with_mapping(
                execution_id=execution_id,
                mapping=private_mapping,
                target_service=action.target_service,
            )
        return self._executor.rollback_change(
            execution_id=execution_id,
            change_id=action.change_id,
            target_service=action.target_service,
        )


def _success(data: dict[str, object]) -> ToolEnvelope:
    return ToolEnvelope(ok=True, tool_call_id=f"tc_{uuid4().hex}", data=data)


def _forbidden(message: str) -> ToolEnvelope:
    return ToolEnvelope(
        ok=False,
        tool_call_id=f"tc_{uuid4().hex}",
        error=ToolError(code="FORBIDDEN", message=message, retryable=False),
    )


def _timeout() -> ToolEnvelope:
    return ToolEnvelope(
        ok=False,
        tool_call_id=f"tc_{uuid4().hex}",
        error=ToolError(
            code="UPSTREAM_TIMEOUT",
            message="bounded action operation timed out",
            retryable=True,
        ),
    )
