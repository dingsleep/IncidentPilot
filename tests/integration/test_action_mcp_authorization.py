from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from incidentpilot.auth.tokens import ApprovalGrant
from incidentpilot.domain.actions import (
    ActionProposal,
    CompensationPlan,
    RestartServiceAction,
    VerificationCheck,
)
from incidentpilot.domain.enums import IncidentStatus, RiskLevel
from incidentpilot.incidents.models import (
    ActionExecutionRow,
    ActionProposalRow,
    ApprovalRow,
    IncidentRow,
)
from incidentpilot.mcp_servers.actions.tools import ActionCallerContext, SqlAlchemyActionStore
from incidentpilot.remediation.adapters.docker import DockerRestartAdapter
from incidentpilot.remediation.executor import ActionExecutor
from incidentpilot.runtime.database import Database
from incidentpilot.telemetry.normalization import canonical_digest
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+psycopg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
ACTION_URL = "postgresql+asyncpg://action_mcp_role:action-local-only@127.0.0.1:5433/incidentpilot"


class FakeContainer:
    def restart(self, *, timeout: int) -> None:
        assert timeout == 30


class FakeDockerClient:
    class Containers:
        @staticmethod
        def get(name: str) -> FakeContainer:
            assert name == "checkout"
            return FakeContainer()

    containers = Containers()


def _alembic() -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", MIGRATION_URL)
    return config


def _proposal() -> ActionProposal:
    return ActionProposal(
        action=RestartServiceAction(target_service="checkout", grace_period_seconds=30),
        risk=RiskLevel.LOW,
        diagnosis_evidence_ids=["ev-one", "ev-two"],
        expected_effect="Restart the approved checkout service.",
        compensation_plan=CompensationPlan(
            mode="not_applicable",
            trigger="none",
            reason="A restart has no safe inverse operation.",
        ),
        verification_checks=[
            VerificationCheck(
                service="checkout",
                metric="error_ratio",
                query_template_id="service_error_ratio",
                comparator="lt",
                threshold=0.05,
                observation_seconds=30,
            )
        ],
        idempotency_key="proposal-key",
    )


@pytest.mark.integration
async def test_action_role_rechecks_server_facts_consumes_nonce_and_replays_idempotently() -> None:
    command.upgrade(_alembic(), "head")
    migration_database = Database(MIGRATION_URL.replace("+psycopg", "+asyncpg"))
    action_database = Database(ACTION_URL)
    incident_id = f"inc-action-{uuid4().hex}"
    proposal_id = f"prop-action-{uuid4().hex}"
    proposal = _proposal()
    payload = proposal.model_dump(mode="json")
    grant = ApprovalGrant(
        tenant_id="local",
        incident_id=incident_id,
        proposal_id=proposal_id,
        proposal_payload_digest=canonical_digest(payload),
        actor_id="local-operator",
        scope="actions:restart",
        nonce=f"nonce-{uuid4().hex}",
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    caller = ActionCallerContext(
        tenant_id="local",
        incident_id=incident_id,
        subject="incidentpilot-api",
        scopes=frozenset({"actions:restart"}),
        approval_grant=grant,
        grant_digest=canonical_digest("contract-grant"),
    )
    executor = ActionExecutor(
        docker=DockerRestartAdapter(
            client=FakeDockerClient(),
            catalog_containers={"checkout": "checkout"},
        ),
        flagd=None,
    )
    store = SqlAlchemyActionStore(database=action_database, executor=executor)
    try:
        await seed_local_data(migration_database)
        async with migration_database.session_factory() as session, session.begin():
            session.add(
                IncidentRow(
                    id=incident_id,
                    tenant_id="local",
                    source="action-contract",
                    external_id=incident_id,
                    status=IncidentStatus.AUTHORIZING.value,
                    severity="P1",
                    title="Action authorization contract",
                )
            )
            await session.flush()
            session.add(
                ActionProposalRow(
                    id=proposal_id,
                    incident_id=incident_id,
                    payload_json=payload,
                    status="APPROVED",
                    policy_result_json={"allowed": True},
                )
            )
            session.add(
                ApprovalRow(
                    id=f"approval-{uuid4().hex}",
                    proposal_id=proposal_id,
                    actor_id="local-operator",
                    decision="APPROVED",
                    reason="contract test",
                    expires_at=grant.expires_at,
                    grant_jws="contract-grant",
                    grant_digest=caller.grant_digest,
                )
            )

        idempotency_key = f"idem-{uuid4().hex}"
        competing_results = await asyncio.gather(
            *(
                store.restart(
                    caller,
                    proposal_id=proposal_id,
                    target_service="checkout",
                    idempotency_key=idempotency_key,
                )
                for _ in range(2)
            )
        )
        succeeded = [
            result
            for result in competing_results
            if result.ok and isinstance(result.data, dict) and result.data["status"] == "succeeded"
        ]
        assert len(succeeded) == 1
        assert all(
            (result.ok and isinstance(result.data, dict) and result.data["status"] == "succeeded")
            or (
                result.ok
                and isinstance(result.data, dict)
                and result.data["status"] == "already_applied"
            )
            or (not result.ok and result.error is not None and result.error.code == "FORBIDDEN")
            for result in competing_results
        )

        replay = await store.restart(
            caller,
            proposal_id=proposal_id,
            target_service="checkout",
            idempotency_key=idempotency_key,
        )
        assert replay.ok is True
        assert isinstance(replay.data, dict)
        assert replay.data["status"] == "already_applied"

        key = f"idem-{uuid4().hex}"
        accepted = await store.restart(
            caller,
            proposal_id=proposal_id,
            target_service="checkout",
            idempotency_key=key,
        )
        assert accepted.ok is False
        assert accepted.error is not None and accepted.error.code == "FORBIDDEN"

        async with migration_database.session_factory() as session:
            approval = await session.scalar(
                select(ApprovalRow).where(ApprovalRow.proposal_id == proposal_id)
            )
            executions = await session.scalar(
                select(func.count())
                .select_from(ActionExecutionRow)
                .where(ActionExecutionRow.proposal_id == proposal_id)
            )
        assert approval is not None and approval.nonce_used_at is not None
        assert executions == 1
    finally:
        await action_database.dispose()
        await migration_database.dispose()
