from __future__ import annotations

# ruff: noqa: ASYNC212
# pyright: reportArgumentType=false, reportMissingTypeStubs=false, reportUnknownMemberType=false
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import SecretStr
from sqlalchemy import select

from incidentpilot.api.main import create_app
from incidentpilot.auth.tokens import ApprovalGrant, DevelopmentApprovalGrantVerifier
from incidentpilot.bootstrap import SqlAlchemyGraphResultSink, open_checkpoint_saver
from incidentpilot.config import ActionSettings, ApiSettings, Settings
from incidentpilot.domain.actions import (
    ActionProposal,
    CompensationPlan,
    RestartServiceAction,
    RollbackChangeAction,
    VerificationCheck,
)
from incidentpilot.domain.enums import IncidentStatus, RiskLevel
from incidentpilot.evaluation.isolation import FlagdScenarioController, episode_environment_lock
from incidentpilot.incidents.models import (
    ActionExecutionRow,
    ActionProposalRow,
    ApprovalRow,
    IncidentRow,
    VerificationResultRow,
)
from incidentpilot.mcp_servers.actions.tools import (
    ActionCallerContext,
    ActionToolHandlers,
    SqlAlchemyActionStore,
)
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope, ToolError
from incidentpilot.orchestration.graph import route_after_execution
from incidentpilot.orchestration.nodes.authorize_action import AuthorizeActionNode
from incidentpilot.orchestration.nodes.execute_action import ExecuteActionNode
from incidentpilot.orchestration.nodes.verify import VerifyNode
from incidentpilot.orchestration.state import IncidentGraphState
from incidentpilot.remediation.action_mcp_client import ApprovedActionMcpClient
from incidentpilot.remediation.adapters.docker import (
    DockerContainer,
    DockerContainerCollection,
    DockerRestartAdapter,
)
from incidentpilot.remediation.adapters.flagd import (
    FlagdChangeMapping,
    FlagdRollbackAdapter,
    InMemoryFlagdChangeMappingStore,
)
from incidentpilot.remediation.authorization_gate import (
    SqlAlchemyApprovalGrantReader,
    SqlAlchemyAuthorizationGate,
)
from incidentpilot.remediation.executor import ActionExecutor
from incidentpilot.remediation.verification import (
    PrometheusVerificationBaselineCollector,
    PrometheusVerificationReader,
    PrometheusVerificationSampler,
    ProposalVerificationService,
    SqlAlchemyVerificationEvidenceRecorder,
)
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.job_queue import PostgresJobQueue, SingleJobQueue
from incidentpilot.telemetry.backends.prometheus import PrometheusBackend
from incidentpilot.telemetry.normalization import canonical_digest
from incidentpilot.telemetry.query_registry import QueryRegistry
from incidentpilot.worker.main import GraphJobHandler
from incidentpilot.worker.processor import JobProcessor
from scripts.seed_local_data import seed_local_data

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
ACTION_URL = "postgresql+asyncpg://action_mcp_role:action-local-only@127.0.0.1:5433/incidentpilot"
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
WORKER_URL = "postgresql+asyncpg://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"
CHECKPOINT_URL = (
    "postgresql://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"
    "?options=-csearch_path%3Dlanggraph_checkpoint"
)
FLAGD_API = "http://127.0.0.1:4000/api"


class _UnusedDockerClient:
    class Container:
        def restart(self, *, timeout: int) -> None:
            del timeout
            raise AssertionError("rollback must not call Docker")

    class Containers:
        def get(self, name: str) -> DockerContainer:
            del name
            return _UnusedDockerClient.Container()

    @property
    def containers(self) -> DockerContainerCollection:
        return self.Containers()


class _FailingDockerClient:
    class Container:
        def restart(self, *, timeout: int) -> None:
            del timeout
            raise RuntimeError("simulated restart failure")

    class Containers:
        def get(self, name: str) -> DockerContainer:
            del name
            return _FailingDockerClient.Container()

    @property
    def containers(self) -> DockerContainerCollection:
        return self.Containers()


def _successful_checkout(client: httpx.Client) -> httpx.Response:
    user_id = uuid4().hex
    client.get("http://127.0.0.1:8080/api/products/0PUK6V6EV0").raise_for_status()
    client.post(
        "http://127.0.0.1:8080/api/cart",
        json={"item": {"productId": "0PUK6V6EV0", "quantity": 1}, "userId": user_id},
    ).raise_for_status()
    return client.post(
        "http://127.0.0.1:8080/api/checkout",
        json={
            "userId": user_id,
            "email": "incidentpilot@example.com",
            "address": {
                "streetAddress": "1600 Amphitheatre Parkway",
                "zipCode": "94043",
                "city": "Mountain View",
                "state": "CA",
                "country": "United States",
            },
            "userCurrency": "USD",
            "creditCard": {
                "creditCardNumber": "4432-8015-6152-0454",
                "creditCardExpirationMonth": 1,
                "creditCardExpirationYear": 2039,
                "creditCardCvv": 672,
            },
        },
    )


async def _await_successful_checkout(client: httpx.Client, *, timeout_seconds: float = 15) -> None:
    deadline = monotonic() + timeout_seconds
    last_response: httpx.Response | None = None
    while monotonic() < deadline:
        last_response = _successful_checkout(client)
        if last_response.status_code == 200:
            return
        await asyncio.sleep(0.5)
    assert last_response is not None and last_response.status_code == 200


def _proposal() -> ActionProposal:
    return ActionProposal(
        action=RollbackChangeAction(target_service="checkout", change_id="chg_payment_unreachable"),
        risk=RiskLevel.LOW,
        diagnosis_evidence_ids=["ev-one", "ev-two"],
        expected_effect="Restore checkout to its server-approved payment route.",
        compensation_plan=CompensationPlan(
            mode="automatic_snapshot_restore",
            trigger="partial_execution_failure",
            reason="The controller owns the complete pre-action flagd snapshot.",
            snapshot_ref="private://flagd/payment-unreachable",
        ),
        verification_checks=[
            VerificationCheck(
                service="checkout",
                metric="error_ratio",
                query_template_id="service_error_ratio",
                comparator="lt",
                threshold=0.02,
                observation_seconds=60,
            )
        ],
        verification_baseline={"checkout:service_error_ratio:error_ratio": 1.0},
        idempotency_key=f"rollback-payment-unreachable-{uuid4().hex}",
    )


def _restart_proposal() -> ActionProposal:
    return ActionProposal(
        action=RestartServiceAction(target_service="checkout", grace_period_seconds=30),
        risk=RiskLevel.LOW,
        diagnosis_evidence_ids=["ev-one", "ev-two"],
        expected_effect="Restart checkout.",
        compensation_plan=CompensationPlan(
            mode="not_applicable", trigger="none", reason="Restart has no safe inverse."
        ),
        verification_checks=[
            VerificationCheck(
                service="checkout",
                metric="error_ratio",
                query_template_id="service_error_ratio",
                comparator="lt",
                threshold=0.02,
                observation_seconds=30,
            )
        ],
        verification_baseline={"checkout:service_error_ratio:error_ratio": 0.0},
        idempotency_key=f"restart-invalid-{uuid4().hex}",
    )


def _keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    return (
        private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode(),
        private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode(),
    )


class _InProcessActionMcpTransport:
    """Test transport that preserves Action MCP's token-to-handler boundary."""

    def __init__(
        self,
        *,
        handlers: ActionToolHandlers,
        grants: DevelopmentApprovalGrantVerifier,
        checkout_client: httpx.Client,
    ) -> None:
        self._handlers = handlers
        self._grants = grants
        self._checkout_client = checkout_client

    async def call(
        self,
        *,
        tool: str,
        arguments: dict[str, object],
        bearer_token: str,
    ) -> ToolEnvelope:
        grant = self._grants.verify_grant(bearer_token)
        if grant is None:
            return ToolEnvelope(
                ok=False,
                tool_call_id=f"tc_{uuid4().hex}",
                error=ToolError(code="FORBIDDEN", message="invalid grant", retryable=False),
            )
        incident_id = arguments.get("incident_id")
        proposal_id = arguments.get("proposal_id")
        idempotency_key = arguments.get("idempotency_key")
        if not all(isinstance(value, str) for value in (incident_id, proposal_id, idempotency_key)):
            raise AssertionError("ApprovedActionMcpClient must send bounded action arguments")
        caller = ActionCallerContext(
            tenant_id=grant.tenant_id,
            incident_id=grant.incident_id,
            subject="incidentpilot-api",
            scopes=frozenset({grant.scope}),
            approval_grant=grant,
            grant_digest=canonical_digest(bearer_token),
        )
        if tool != "rollback_change" or arguments.get("change_id") != "chg_payment_unreachable":
            raise AssertionError("the payment-unreachable proposal must use rollback_change")
        response = await self._handlers.rollback_change(
            caller,
            proposal_id=proposal_id,
            change_id="chg_payment_unreachable",
            idempotency_key=idempotency_key,
        )
        if (
            response.ok
            and isinstance(response.data, dict)
            and response.data.get("status") == "succeeded"
        ):
            await _await_successful_checkout(self._checkout_client)
        return response


@pytest.mark.e2e
async def test_approved_rollback_restores_real_payment_unreachable_flag() -> None:
    migration_database = Database(MIGRATION_URL)
    action_database = Database(ACTION_URL)
    incident_id = f"inc-approved-rollback-{uuid4().hex}"
    proposal_id = f"prop-approved-rollback-{uuid4().hex}"
    client = httpx.Client(timeout=20, trust_env=False)
    controller = FlagdScenarioController(client=client, base_url=FLAGD_API)
    try:
        await seed_local_data(migration_database)
        with (
            episode_environment_lock(),
            controller.activate("paymentUnreachable", "on") as original,
        ):
            proposal = _proposal()
            payload = proposal.model_dump(mode="json")
            grant = ApprovalGrant(
                tenant_id="local",
                incident_id=incident_id,
                proposal_id=proposal_id,
                proposal_payload_digest=canonical_digest(payload),
                actor_id="local-operator",
                scope="actions:rollback-change",
                nonce=f"nonce-{uuid4().hex}",
                issued_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            caller = ActionCallerContext(
                tenant_id="local",
                incident_id=incident_id,
                subject="incidentpilot-api",
                scopes=frozenset({"actions:rollback-change"}),
                approval_grant=grant,
                grant_digest=canonical_digest("e2e-approved-grant"),
            )
            async with migration_database.session_factory() as session, session.begin():
                session.add(
                    IncidentRow(
                        id=incident_id,
                        tenant_id="local",
                        source="approved-remediation-e2e",
                        external_id=incident_id,
                        status=IncidentStatus.AUTHORIZING.value,
                        severity="P1",
                        title="Approved payment unreachable rollback",
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
                        reason="e2e approval",
                        expires_at=grant.expires_at,
                        grant_jws="e2e-approved-grant",
                        grant_digest=caller.grant_digest,
                    )
                )

            executor = ActionExecutor(
                docker=DockerRestartAdapter(
                    client=_UnusedDockerClient(), catalog_containers={"checkout": "checkout"}
                ),
                flagd=FlagdRollbackAdapter(
                    controller=controller,
                    mappings=InMemoryFlagdChangeMappingStore(
                        [
                            FlagdChangeMapping(
                                change_id="chg_payment_unreachable",
                                target_service="checkout",
                                flag_name="paymentUnreachable",
                                restore_config=original.config,
                                restore_digest=original.digest,
                            )
                        ]
                    ),
                ),
            )
            response = await SqlAlchemyActionStore(
                database=action_database, executor=executor
            ).rollback(
                caller,
                proposal_id=proposal_id,
                change_id="chg_payment_unreachable",
                idempotency_key=proposal.idempotency_key,
            )

            assert response.ok is True
            assert isinstance(response.data, dict)
            assert response.data["status"] == "succeeded"
            assert controller.snapshot().digest == original.digest
            assert _successful_checkout(client).status_code == 200
            registry = QueryRegistry.from_files(
                metrics_path=Path("query_templates/metrics.yaml"),
                logs_path=Path("query_templates/logs.yaml"),
                allowed_services={"checkout"},
            )
            async with httpx.AsyncClient(timeout=20, trust_env=False) as metrics_client:
                sampler = PrometheusVerificationSampler(
                    metrics=PrometheusBackend(client=metrics_client, registry=registry)
                )
                verification = await ProposalVerificationService(
                    reader=PrometheusVerificationReader(
                        sampler=sampler,
                        evidence=SqlAlchemyVerificationEvidenceRecorder(
                            database=migration_database
                        ),
                    ),
                    wait=asyncio.sleep,
                ).verify(incident_id=incident_id, proposal=proposal)
            assert verification.recovered is True
            assert verification.evidence_ids
            async with migration_database.session_factory() as session:
                execution = await session.scalar(
                    select(ActionExecutionRow).where(ActionExecutionRow.proposal_id == proposal_id)
                )
            assert execution is not None and execution.status == "succeeded"
    finally:
        client.close()
        await action_database.dispose()
        await migration_database.dispose()


@pytest.mark.e2e
async def test_approved_but_invalid_restart_records_failure_and_routes_to_needs_human() -> None:
    migration_database = Database(MIGRATION_URL)
    action_database = Database(ACTION_URL)
    incident_id = f"inc-invalid-restart-{uuid4().hex}"
    proposal_id = f"prop-invalid-restart-{uuid4().hex}"
    proposal = _restart_proposal()
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
        grant_digest=canonical_digest("e2e-invalid-restart-grant"),
    )
    try:
        await seed_local_data(migration_database)
        async with migration_database.session_factory() as session, session.begin():
            session.add(
                IncidentRow(
                    id=incident_id,
                    tenant_id="local",
                    source="approved-remediation-e2e",
                    external_id=incident_id,
                    status=IncidentStatus.AUTHORIZING.value,
                    severity="P1",
                    title="Approved invalid restart",
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
                    reason="e2e approval",
                    expires_at=grant.expires_at,
                    grant_jws="e2e-invalid-restart-grant",
                    grant_digest=caller.grant_digest,
                )
            )

        response = await SqlAlchemyActionStore(
            database=action_database,
            executor=ActionExecutor(
                docker=DockerRestartAdapter(
                    client=_FailingDockerClient(), catalog_containers={"checkout": "checkout"}
                ),
                flagd=None,
            ),
        ).restart(
            caller,
            proposal_id=proposal_id,
            target_service="checkout",
            idempotency_key=proposal.idempotency_key,
        )

        assert response.ok is True
        assert isinstance(response.data, dict)
        assert response.data["status"] == "failed"
        assert (
            route_after_execution({"status": IncidentStatus.ACTION_FAILED.value})
            == "mark_needs_human"
        )
        async with migration_database.session_factory() as session:
            execution = await session.scalar(
                select(ActionExecutionRow).where(ActionExecutionRow.proposal_id == proposal_id)
            )
        assert execution is not None and execution.status == "failed"
    finally:
        await action_database.dispose()
        await migration_database.dispose()


@pytest.mark.e2e
async def test_api_approved_resume_authorizes_rolls_back_verifies_and_resolves() -> None:
    """Prove the persisted approval path against the local OTel Demo, end to end."""
    migration_database = Database(MIGRATION_URL)
    action_database = Database(ACTION_URL)
    worker_database = Database(WORKER_URL)
    incident_id = f"inc-resume-rollback-{uuid4().hex}"
    proposal_id = f"prop-resume-rollback-{uuid4().hex}"
    private_key, public_key = _keypair()
    proposal = _proposal()
    client = httpx.Client(timeout=20, trust_env=False)
    controller = FlagdScenarioController(client=client, base_url=FLAGD_API)
    app = create_app(
        Settings(
            environment="development",
            api=ApiSettings(database_url=SecretStr(API_URL)),
            actions=ActionSettings(approval_signing_key=SecretStr(private_key)),
        )
    )
    graph = StateGraph(IncidentGraphState)

    async def await_approval(state: dict[str, Any]) -> dict[str, Any]:
        approval_id = interrupt({"proposal_id": state["action_proposal_id"]})
        return {
            "approval_reference_id": approval_id,
            "status": IncidentStatus.AUTHORIZING.value,
        }

    try:
        await seed_local_data(migration_database)
        with (
            episode_environment_lock(),
            controller.activate("paymentUnreachable", "on") as original,
        ):
            registry = QueryRegistry.from_files(
                metrics_path=Path("query_templates/metrics.yaml"),
                logs_path=Path("query_templates/logs.yaml"),
                allowed_services={"checkout"},
            )
            async with httpx.AsyncClient(timeout=20, trust_env=False) as metrics_client:
                sampler = PrometheusVerificationSampler(
                    metrics=PrometheusBackend(client=metrics_client, registry=registry)
                )
                baseline = await PrometheusVerificationBaselineCollector(
                    sampler=sampler
                ).capture(proposal=proposal)
            proposal = proposal.model_copy(update={"verification_baseline": baseline})
            payload = proposal.model_dump(mode="json")
            async with migration_database.session_factory() as session, session.begin():
                session.add(
                    IncidentRow(
                        id=incident_id,
                        tenant_id="local",
                        source="approved-remediation-e2e",
                        external_id=incident_id,
                        status=IncidentStatus.WAITING_APPROVAL.value,
                        severity="P1",
                        title="API-approved payment-unreachable rollback",
                    )
                )
                await session.flush()
                session.add(
                    ActionProposalRow(
                        id=proposal_id,
                        incident_id=incident_id,
                        payload_json=payload,
                        status="PENDING_APPROVAL",
                        policy_result_json={"allowed": True},
                    )
                )

            executor = ActionExecutor(
                docker=DockerRestartAdapter(
                    client=_UnusedDockerClient(), catalog_containers={"checkout": "checkout"}
                ),
                flagd=FlagdRollbackAdapter(
                    controller=controller,
                    mappings=InMemoryFlagdChangeMappingStore(
                        [
                            FlagdChangeMapping(
                                change_id="chg_payment_unreachable",
                                target_service="checkout",
                                flag_name="paymentUnreachable",
                                restore_config=original.config,
                                restore_digest=original.digest,
                            )
                        ]
                    ),
                ),
            )
            grant_verifier = DevelopmentApprovalGrantVerifier(
                issuer="https://incidentpilot.local",
                audience="action-mcp",
                public_key=public_key,
            )
            actions = ApprovedActionMcpClient(
                grants=SqlAlchemyApprovalGrantReader(database=worker_database),
                transport=_InProcessActionMcpTransport(
                    handlers=ActionToolHandlers(
                        store=SqlAlchemyActionStore(database=action_database, executor=executor)
                    ),
                    grants=grant_verifier,
                    checkout_client=client,
                ),
            )
            async with httpx.AsyncClient(timeout=20, trust_env=False) as metrics_client:
                verifier = ProposalVerificationService(
                    reader=PrometheusVerificationReader(
                        sampler=PrometheusVerificationSampler(
                            metrics=PrometheusBackend(client=metrics_client, registry=registry)
                        ),
                        evidence=SqlAlchemyVerificationEvidenceRecorder(
                            database=migration_database
                        ),
                    ),
                    wait=asyncio.sleep,
                )
                graph.add_node("await_approval", await_approval)
                graph.add_node(
                    "authorize_action",
                    AuthorizeActionNode(
                        gate=SqlAlchemyAuthorizationGate(
                            database=worker_database,
                            grants=grant_verifier,
                        )
                    ),
                )
                graph.add_node("execute_action", ExecuteActionNode(actions=actions))
                graph.add_node("verify", VerifyNode(verifier=verifier))
                graph.add_edge(START, "await_approval")
                graph.add_edge("await_approval", "authorize_action")
                graph.add_edge("authorize_action", "execute_action")
                graph.add_edge("execute_action", "verify")
                graph.add_edge("verify", END)

                async with open_checkpoint_saver(CHECKPOINT_URL, setup=True) as saver:
                    compiled = graph.compile(checkpointer=saver)
                    await compiled.ainvoke(
                        {
                            "incident_id": incident_id,
                            "tenant_id": "local",
                            "status": IncidentStatus.WAITING_APPROVAL.value,
                            "action_proposal_id": proposal_id,
                            "action_proposal": payload,
                        },
                        {"configurable": {"thread_id": incident_id}},
                    )
                    async with app.router.lifespan_context(app):
                        transport = httpx.ASGITransport(app=app)
                        async with httpx.AsyncClient(
                            transport=transport, base_url="http://test"
                        ) as api_client:
                            approval_response = await api_client.post(
                                f"/api/v1/incidents/{incident_id}/proposals/{proposal_id}/approval",
                                json={
                                    "decision": "approve",
                                    "reason": "Bounded rollback approved after review.",
                                },
                                headers={"X-IncidentPilot-Actor": "local-operator"},
                            )
                    assert approval_response.status_code == 202
                    queue = PostgresJobQueue(worker_database)
                    job_id = approval_response.json()["job_id"]
                    processor = JobProcessor(
                        queue=SingleJobQueue(queue, job_id),
                        worker_id=f"worker-{uuid4().hex}",
                        handler=GraphJobHandler(
                            graph=graph.compile(checkpointer=saver),
                            initial_state=_UnexpectedInitialState(),
                            result_sink=SqlAlchemyGraphResultSink(
                                migration_database,
                                model_profile="e2e",
                                prompt_version="m7.5",
                            ),
                        ),
                    )
                    assert await processor.run_once()
                    job = await queue.get(job_id)
                    assert job is not None and job.status == "completed"

            assert controller.snapshot().digest == original.digest
            async with migration_database.session_factory() as session:
                incident = await session.get(IncidentRow, incident_id)
                execution = await session.scalar(
                    select(ActionExecutionRow).where(ActionExecutionRow.proposal_id == proposal_id)
                )
                verification_row = await session.scalar(
                    select(VerificationResultRow)
                    .join(
                        ActionExecutionRow,
                        ActionExecutionRow.id == VerificationResultRow.execution_id,
                    )
                    .where(ActionExecutionRow.proposal_id == proposal_id)
                )
            assert incident is not None and incident.status == IncidentStatus.RESOLVED.value
            assert execution is not None and execution.status == "succeeded"
            assert verification_row is not None
            assert verification_row.payload_json["recovered"] is True
    finally:
        client.close()
        await worker_database.dispose()
        await action_database.dispose()
        await migration_database.dispose()


class _UnexpectedInitialState:
    async def load(self, incident_id: str) -> dict[str, Any]:
        del incident_id
        raise AssertionError("a RESUME job must continue its stored checkpoint")
