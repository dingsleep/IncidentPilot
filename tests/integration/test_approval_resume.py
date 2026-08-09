from __future__ import annotations

# pyright: reportArgumentType=false, reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import SecretStr

from incidentpilot.api.main import create_app
from incidentpilot.bootstrap import open_checkpoint_saver
from incidentpilot.config import ActionSettings, ApiSettings, Settings
from incidentpilot.domain.actions import (
    ActionProposal,
    ActionResult,
    CompensationPlan,
    RestartServiceAction,
    VerificationCheck,
)
from incidentpilot.domain.enums import IncidentStatus, RiskLevel
from incidentpilot.incidents.models import ActionProposalRow, IncidentRow
from incidentpilot.orchestration.nodes.authorize_action import AuthorizeActionNode
from incidentpilot.orchestration.nodes.execute_action import ExecuteActionNode
from incidentpilot.orchestration.state import IncidentGraphState
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.job_queue import ClaimedJob, PostgresJobQueue, SingleJobQueue
from incidentpilot.worker.main import GraphJobHandler
from incidentpilot.worker.processor import JobProcessor
from scripts.seed_local_data import seed_local_data

CHECKPOINT_URL = (
    "postgresql://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"
    "?options=-csearch_path%3Dlanggraph_checkpoint"
)
MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
WORKER_URL = "postgresql+asyncpg://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"


def _private_key() -> str:
    return (
        Ed25519PrivateKey.generate()
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )


def _proposal() -> dict[str, object]:
    return ActionProposal(
        action=RestartServiceAction(target_service="checkout", grace_period_seconds=30),
        risk=RiskLevel.LOW,
        diagnosis_evidence_ids=["ev-one", "ev-two"],
        expected_effect="Restart checkout.",
        compensation_plan=CompensationPlan(
            mode="not_applicable", trigger="none", reason="No safe inverse restart exists."
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
        idempotency_key="resume-integration-action",
    ).model_dump(mode="json")


@pytest.mark.integration
async def test_resume_job_uses_approval_reference_to_continue_dynamic_interrupt() -> None:
    incident_id = f"inc-approval-resume-{uuid4().hex}"
    expected_incident_id = incident_id
    config = {"configurable": {"thread_id": incident_id}}
    graph = StateGraph(dict)

    async def await_approval(state: dict[str, Any]) -> dict[str, Any]:
        approval_id = interrupt({"proposal_id": state["proposal_id"]})
        return {
            "approval_id": approval_id,
            "status": "AUTHORIZED",
        }

    graph.add_node("await_approval", await_approval)
    graph.add_edge(START, "await_approval")
    graph.add_edge("await_approval", END)

    async with open_checkpoint_saver(CHECKPOINT_URL, setup=True) as saver:
        compiled = graph.compile(checkpointer=saver)
        await compiled.ainvoke({"proposal_id": "proposal-1"}, config)

        class UnexpectedInitialState:
            async def load(self, incident_id: str) -> dict[str, Any]:
                del incident_id
                raise AssertionError("resume must use the persisted checkpoint")

        class CapturingSink:
            state: dict[str, Any] | None = None

            async def persist(self, incident_id: str, state: dict[str, Any]) -> None:
                assert incident_id == expected_incident_id
                self.state = state

        sink = CapturingSink()
        await GraphJobHandler(
            graph=compiled,
            initial_state=UnexpectedInitialState(),
            result_sink=sink,
        )(
            ClaimedJob(
                id=f"job-{uuid4().hex}",
                incident_id=incident_id,
                job_type="RESUME",
                resume_reference_id="approval-123",
                attempts=1,
            )
        )

    assert sink.state == {
        "approval_id": "approval-123",
        "status": "AUTHORIZED",
    }


@pytest.mark.integration
async def test_api_approval_job_resumes_the_persisted_graph_checkpoint() -> None:
    migration_database = Database(MIGRATION_URL)
    worker_database = Database(WORKER_URL)
    incident_id = f"inc-api-resume-{uuid4().hex}"
    expected_incident_id = incident_id
    proposal_id = f"proposal-api-resume-{uuid4().hex}"
    app = create_app(
        Settings(
            environment="development",
            api=ApiSettings(database_url=SecretStr(API_URL)),
            actions=ActionSettings(approval_signing_key=SecretStr(_private_key())),
        )
    )
    graph = StateGraph(IncidentGraphState)
    authorization_calls: list[tuple[str, str, str]] = []
    execution_calls: list[tuple[str, str, str]] = []

    async def await_approval(state: dict[str, Any]) -> dict[str, Any]:
        approval_id = interrupt({"proposal_id": state["action_proposal_id"]})
        return {
            "approval_reference_id": approval_id,
            "status": IncidentStatus.AUTHORIZING.value,
        }

    class Gate:
        async def authorize(self, *, incident_id: str, proposal_id: str, approval_id: str) -> None:
            authorization_calls.append((incident_id, proposal_id, approval_id))

    class Actions:
        async def execute(
            self,
            *,
            incident_id: str,
            proposal_id: str,
            proposal: ActionProposal,
            approval_id: str,
        ) -> ActionResult:
            del proposal
            execution_calls.append((incident_id, proposal_id, approval_id))
            now = datetime.now(UTC)
            return ActionResult(
                proposal_id=proposal_id,
                execution_id="exec-resume",
                status="succeeded",
                started_at=now,
                finished_at=now,
            )

    graph.add_node("await_approval", await_approval)
    graph.add_node("authorize_action", AuthorizeActionNode(gate=Gate()))
    graph.add_node("execute_action", ExecuteActionNode(actions=Actions()))
    graph.add_edge(START, "await_approval")
    graph.add_edge("await_approval", "authorize_action")
    graph.add_edge("authorize_action", "execute_action")
    graph.add_edge("execute_action", END)
    try:
        await seed_local_data(migration_database)
        async with migration_database.session_factory() as session, session.begin():
            session.add(
                IncidentRow(
                    id=incident_id,
                    tenant_id="local",
                    source="api-resume-test",
                    external_id=incident_id,
                    status=IncidentStatus.WAITING_APPROVAL.value,
                    severity="P1",
                    title="API resume test",
                )
            )
            await session.flush()
            session.add(
                ActionProposalRow(
                    id=proposal_id,
                    incident_id=incident_id,
                    payload_json=_proposal(),
                    status="PENDING_APPROVAL",
                    policy_result_json={"allowed": True},
                )
            )

        async with open_checkpoint_saver(CHECKPOINT_URL, setup=True) as saver:
            compiled = graph.compile(checkpointer=saver)
            await compiled.ainvoke(
                {
                    "incident_id": incident_id,
                    "action_proposal_id": proposal_id,
                    "action_proposal": _proposal(),
                },
                {"configurable": {"thread_id": incident_id}},
            )
            snapshot = await compiled.aget_state({"configurable": {"thread_id": incident_id}})
            assert snapshot.values["incident_id"] == incident_id
            assert snapshot.values["action_proposal_id"] == proposal_id
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        f"/api/v1/incidents/{incident_id}/proposals/{proposal_id}/approval",
                        json={"decision": "approve", "reason": "Bounded action is approved."},
                        headers={"X-IncidentPilot-Actor": "local-operator"},
                    )
            assert response.status_code == 202

            class UnexpectedInitialState:
                async def load(self, incident_id: str) -> dict[str, Any]:
                    del incident_id
                    raise AssertionError("resume must not reconstruct initial state")

            class CapturingSink:
                state: dict[str, Any] | None = None

                async def persist(self, incident_id: str, state: dict[str, Any]) -> None:
                    assert incident_id == expected_incident_id
                    self.state = state

            sink = CapturingSink()
            queue = PostgresJobQueue(worker_database)
            resumed_graph = graph.compile(checkpointer=saver)
            processor = JobProcessor(
                queue=SingleJobQueue(queue, response.json()["job_id"]),
                worker_id=f"worker-{uuid4().hex}",
                handler=GraphJobHandler(
                    graph=resumed_graph,
                    initial_state=UnexpectedInitialState(),
                    result_sink=sink,
                ),
            )
            pending = await queue.get(response.json()["job_id"])
            assert pending is not None
            assert pending.resume_reference_id == response.json()["approval_id"]
            assert await processor.run_once()
            job = await queue.get(response.json()["job_id"])
            assert job is not None and job.status == "completed"
            assert sink.state is not None, await compiled.aget_state(
                {"configurable": {"thread_id": incident_id}}
            )
            assert sink.state["status"] == IncidentStatus.EXECUTING.value
            assert sink.state["approval_reference_id"].startswith("approval_")
            assert authorization_calls == [
                (incident_id, proposal_id, response.json()["approval_id"])
            ]
            assert execution_calls == [(incident_id, proposal_id, response.json()["approval_id"])]
    finally:
        await worker_database.dispose()
        await migration_database.dispose()
