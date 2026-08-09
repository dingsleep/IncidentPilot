from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from incidentpilot.bootstrap import (
    DatabaseInitialStateLoader,
    DatabaseReferenceStore,
    SqlAlchemyGraphResultSink,
    open_checkpoint_saver,
)
from incidentpilot.domain.alerts import AlertPayload, TimeRange
from incidentpilot.domain.diagnosis import (
    Diagnosis,
    InvestigationFinding,
    InvestigationReport,
    RootCauseHypothesis,
)
from incidentpilot.domain.enums import EvidenceKind, IncidentStatus, Severity
from incidentpilot.evaluation.isolation import FlagdScenarioController
from incidentpilot.incidents.models import DiagnosisRow, IncidentRow
from incidentpilot.incidents.service import IncidentService
from incidentpilot.mcp_servers.common.auth import CallerContext
from incidentpilot.mcp_servers.telemetry.resources import load_service_catalog
from incidentpilot.mcp_servers.telemetry.tools import TelemetryToolHandlers
from incidentpilot.orchestration.graph import ReadOnlyGraphNodes, compile_read_only_graph
from incidentpilot.orchestration.nodes.investigate_metrics import InvestigationNode
from incidentpilot.orchestration.nodes.prepare_context import PrepareContextNode
from incidentpilot.orchestration.nodes.report import ReportNode
from incidentpilot.orchestration.nodes.synthesize import SynthesizeNode
from incidentpilot.orchestration.nodes.triage import TriageNode
from incidentpilot.orchestration.state import (
    IncidentIdentity,
    InvestigationTask,
    PreparedContext,
    ServiceContext,
    SynthesisDraft,
    TriageDecision,
)
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.job_queue import PostgresJobQueue, SingleJobQueue
from incidentpilot.telemetry.backends.jaeger import JaegerBackend
from incidentpilot.telemetry.backends.opensearch import OpenSearchBackend
from incidentpilot.telemetry.backends.prometheus import PrometheusBackend
from incidentpilot.telemetry.query_registry import QueryRegistry
from incidentpilot.telemetry.schemas import MetricQuery, TraceSearch
from incidentpilot.worker.main import GraphJobHandler
from incidentpilot.worker.processor import JobProcessor
from scripts.seed_local_data import seed_local_data

ROOT = Path(__file__).parents[2]
FRONTEND = "http://127.0.0.1:8080"
MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
TELEMETRY_URL = (
    "postgresql+asyncpg://telemetry_mcp_role:telemetry-local-only@127.0.0.1:5433/incidentpilot"
)
WORKER_URL = "postgresql+asyncpg://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"
CHECKPOINT_URL = (
    "postgresql://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"
    "?options=-csearch_path%3Dlanggraph_checkpoint"
)


def _checkout(client: httpx.Client) -> httpx.Response:
    user_id = str(uuid.uuid4())
    product_id = "0PUK6V6EV0"
    product = client.get(f"{FRONTEND}/api/products/{product_id}")
    product.raise_for_status()
    client.post(
        f"{FRONTEND}/api/cart",
        json={"item": {"productId": product_id, "quantity": 1}, "userId": user_id},
    ).raise_for_status()
    return client.post(
        f"{FRONTEND}/api/checkout",
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


class ServerContextLoader:
    def __init__(self, *, incident_id: str, catalog: list[ServiceContext]) -> None:
        self._incident_id = incident_id
        self._catalog = catalog

    async def get_incident_identity(self, incident_id: str) -> IncidentIdentity | None:
        if incident_id != self._incident_id:
            return None
        return IncidentIdentity(incident_id=incident_id, tenant_id="local")

    async def load_service_catalog(self) -> list[ServiceContext]:
        return self._catalog

    async def load_recent_changes(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        services: list[str],
        time_range: TimeRange,
    ) -> list[Any]:
        assert tenant_id == "local"
        assert incident_id == self._incident_id
        assert services == ["checkout"]
        assert time_range.end >= time_range.start
        return []


class ScriptedTriageAgent:
    async def triage(self, context: PreparedContext) -> TriageDecision:
        assert {service.name for service in context.services} >= {"checkout", "payment"}
        return TriageDecision(
            scoped_services=["checkout", "payment"],
            investigators=["metrics", "traces"],
            objectives={
                "metrics": "Check payment process health",
                "traces": "Find the failing checkout dependency",
            },
        )


class ScriptedMetricsAgent:
    def __init__(self, handlers: TelemetryToolHandlers, caller: CallerContext) -> None:
        self._handlers = handlers
        self._caller = caller

    async def investigate(self, task: InvestigationTask) -> InvestigationReport:
        end = datetime.now(UTC)
        envelope = await self._handlers.query_metrics(
            self._caller,
            MetricQuery(
                template_id="service_request_rate",
                service="payment",
                start=end - timedelta(minutes=10),
                end=end,
                step_seconds=15,
            ),
        )
        if not envelope.ok or not envelope.evidence_id:
            raise RuntimeError(f"metric query failed: {envelope.error}")
        return InvestigationReport(
            investigator="metrics",
            scope_services=task.scope_services,
            findings=[
                InvestigationFinding(
                    statement="Payment process telemetry is present during the failure window",
                    evidence_ids=[envelope.evidence_id],
                    signal_strength=0.6,
                )
            ],
            tool_call_ids=[envelope.tool_call_id],
        )


class ScriptedTracesAgent:
    def __init__(self, handlers: TelemetryToolHandlers, caller: CallerContext) -> None:
        self._handlers = handlers
        self._caller = caller

    async def investigate(self, task: InvestigationTask) -> InvestigationReport:
        envelope = None
        for _ in range(10):
            end = datetime.now(UTC)
            envelope = await self._handlers.search_traces(
                self._caller,
                TraceSearch(
                    services=["checkout", "payment"],
                    start=end - timedelta(minutes=10),
                    end=end,
                    error_only=True,
                    limit=20,
                ),
            )
            data = cast(dict[str, Any], envelope.data or {})
            if envelope.ok and data.get("traces"):
                break
            await asyncio.sleep(1)
        if envelope is None or not envelope.ok or not envelope.evidence_id:
            raise RuntimeError(f"trace query failed: {envelope.error if envelope else None}")
        data = cast(dict[str, Any], envelope.data or {})
        if not data.get("traces"):
            raise RuntimeError("no error traces observed")
        return InvestigationReport(
            investigator="traces",
            scope_services=task.scope_services,
            findings=[
                InvestigationFinding(
                    statement="Error traces cross checkout and payment",
                    evidence_ids=[envelope.evidence_id],
                    signal_strength=0.95,
                )
            ],
            tool_call_ids=[envelope.tool_call_id],
        )


class ScriptedSynthesisAgent:
    async def synthesize(self, state: dict[str, Any]) -> SynthesisDraft:
        evidence_ids = list(cast(list[str], state["evidence_ids"]))
        return SynthesisDraft(
            hypotheses=[
                RootCauseHypothesis(
                    id="hyp-payment",
                    root_cause_service="payment",
                    failure_mode="Payment dependency rejects checkout charge requests",
                    confidence=0.9,
                    supporting_evidence_ids=evidence_ids,
                )
            ],
            diagnosis=Diagnosis(
                symptom_service="checkout",
                root_cause_service="payment",
                dependency_service="payment",
                root_cause_category="dependency_failure",
                root_cause_summary="Checkout failures originate at the payment dependency",
                confidence=0.9,
                evidence_ids=evidence_ids,
                customer_impact="Checkout requests fail",
            ),
        )


def _catalog() -> list[ServiceContext]:
    raw = load_service_catalog(ROOT / "service_catalog" / "otel-demo.yaml")
    services = cast(list[dict[str, Any]], raw["services"])
    return [
        ServiceContext(
            name=str(item["name"]),
            dependencies=[str(value) for value in cast(list[Any], item["dependencies"])],
            owner=str(item["owner"]),
            criticality=str(item["criticality"]),
        )
        for item in services
    ]


@pytest.mark.e2e
async def test_real_fault_reaches_grounded_read_only_report_through_worker() -> None:
    incident_id = f"inc-e2e-{uuid4().hex}"
    job_id = f"job-e2e-{uuid4().hex}"
    migration_database = Database(MIGRATION_URL)
    api_database = Database(API_URL)
    telemetry_database = Database(TELEMETRY_URL)
    worker_database = Database(WORKER_URL)
    async_client = httpx.AsyncClient(timeout=20, trust_env=False)
    try:
        await seed_local_data(migration_database)
        now = datetime.now(UTC)
        await IncidentService(api_database).create_with_start_job(
            incident_id=incident_id,
            tenant_id="local",
            alert=AlertPayload(
                external_id=incident_id,
                source="e2e",
                title="Checkout failures",
                description="Checkout returns server errors",
                severity=Severity.P1,
                starts_at=now,
                service_hint="checkout",
            ),
            job_id=job_id,
            available_at=now,
        )

        registry = QueryRegistry.from_files(
            metrics_path=ROOT / "query_templates" / "metrics.yaml",
            logs_path=ROOT / "query_templates" / "logs.yaml",
            allowed_services={service.name for service in _catalog()},
        )
        handlers = TelemetryToolHandlers(
            database=telemetry_database,
            registry=registry,
            metrics=PrometheusBackend(client=async_client, registry=registry),
            logs=OpenSearchBackend(client=async_client),
            traces=JaegerBackend(client=async_client),
        )
        caller = CallerContext(
            tenant_id="local",
            incident_id=incident_id,
            subject="e2e-investigator",
            scopes=frozenset({"telemetry:metrics.read", "telemetry:traces.read"}),
        )
        references = DatabaseReferenceStore(worker_database)

        async def unused(state: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError(f"unexpected investigator: {state['task']}")

        nodes = ReadOnlyGraphNodes(
            prepare_context=PrepareContextNode(
                ServerContextLoader(incident_id=incident_id, catalog=_catalog())
            ),
            triage=TriageNode(ScriptedTriageAgent()),
            investigate_metrics=InvestigationNode(
                investigator="metrics",
                agent=ScriptedMetricsAgent(handlers, caller),
                references=references,
            ),
            investigate_logs=unused,
            investigate_traces=InvestigationNode(
                investigator="traces",
                agent=ScriptedTracesAgent(handlers, caller),
                references=references,
            ),
            investigate_runbooks=unused,
            synthesize=SynthesizeNode(ScriptedSynthesisAgent(), references),
            report=ReportNode(),
        )

        with httpx.Client(timeout=20, trust_env=False) as sync_client:
            controller = FlagdScenarioController(
                client=sync_client,
                poll_interval=0.5,
                timeout=15,
            )
            original = controller.snapshot()
            with controller.activate("paymentFailure", "100%"):
                await asyncio.sleep(3)
                responses = [_checkout(sync_client) for _ in range(3)]
                statuses = [response.status_code for response in responses]
                assert all(status >= 500 for status in statuses), statuses
                await asyncio.sleep(3)

                async with open_checkpoint_saver(CHECKPOINT_URL, setup=True) as saver:
                    graph = compile_read_only_graph(nodes, checkpointer=saver)
                    handler = GraphJobHandler(
                        graph=graph,
                        initial_state=DatabaseInitialStateLoader(worker_database),
                        result_sink=SqlAlchemyGraphResultSink(
                            worker_database,
                            model_profile="scripted-e2e",
                            prompt_version="v1",
                        ),
                    )
                    processor = JobProcessor(
                        queue=SingleJobQueue(PostgresJobQueue(worker_database), job_id),
                        worker_id="e2e-worker",
                        handler=handler,
                    )
                    assert await processor.run_once()
                    snapshot = await graph.aget_state({"configurable": {"thread_id": incident_id}})
                    history = [
                        item
                        async for item in graph.aget_state_history(
                            {"configurable": {"thread_id": incident_id}}
                        )
                    ]
            assert controller.snapshot().digest == original.digest

        state = cast(dict[str, Any], snapshot.values)
        assert state["status"] == IncidentStatus.REPORTING.value
        diagnosis = Diagnosis.model_validate(state["diagnosis"])
        assert diagnosis.root_cause_service == "payment"
        assert any(
            item.values.get("status") == IncidentStatus.RESOLVED_READ_ONLY.value for item in history
        )

        evidence = [
            await references.get_evidence(evidence_id) for evidence_id in diagnosis.evidence_ids
        ]
        assert all(item is not None for item in evidence)
        typed_evidence = [item for item in evidence if item is not None]
        assert {item.kind for item in typed_evidence} >= {
            EvidenceKind.METRIC,
            EvidenceKind.TRACE,
        }
        assert all(item.source_uri and urlparse(item.source_uri).scheme for item in typed_evidence)
        resources = [
            cast(
                dict[str, Any],
                await handlers.get_evidence_resource(
                    caller,
                    incident_id=incident_id,
                    evidence_id=item.id,
                ),
            )
            for item in typed_evidence
        ]
        assert all(resource["raw_json"] for resource in resources)

        job = await PostgresJobQueue(worker_database).get(job_id)
        assert job is not None and job.status == "completed"
        async with worker_database.session_factory() as session:
            incident = await session.get(IncidentRow, incident_id)
            stored_diagnosis = (
                await session.scalars(
                    select(DiagnosisRow).where(DiagnosisRow.incident_id == incident_id)
                )
            ).one()
        assert incident is not None and incident.status == IncidentStatus.REPORTING.value
        assert stored_diagnosis.payload_json["root_cause_service"] == "payment"
    finally:
        await async_client.aclose()
        await worker_database.dispose()
        await telemetry_database.dispose()
        await api_database.dispose()
        await migration_database.dispose()
