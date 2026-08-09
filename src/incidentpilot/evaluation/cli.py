from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

import httpx
import yaml
from opentelemetry.sdk.trace import TracerProvider

from incidentpilot.bootstrap import SqlAlchemyGraphResultSink
from incidentpilot.config import LlmSettings, ModelSettings
from incidentpilot.domain.alerts import AlertPayload
from incidentpilot.domain.diagnosis import Diagnosis, RootCauseHypothesis
from incidentpilot.domain.enums import ExecutionMode, IncidentStatus
from incidentpilot.evaluation.isolation import FlagdScenarioController
from incidentpilot.evaluation.loader import (
    LoadedEpisode,
    RecoveryCheck,
    RuntimeEpisodeInput,
    TrafficSpec,
    load_episode_suite,
)
from incidentpilot.evaluation.metrics import (
    CaseScore,
    EvaluationFactRepository,
    EvaluationFacts,
    EvaluationMode,
    EvaluationResultStore,
    RunAggregate,
    aggregate_run,
)
from incidentpilot.evaluation.report import write_mode_report
from incidentpilot.evaluation.runner import (
    EnvironmentMetadata,
    EpisodeRunner,
    HealthSnapshot,
)
from incidentpilot.evaluation.scorer import SCORER_VERSION, score_case
from incidentpilot.evaluation.taxonomy import (
    TAXONOMY_POLICY_VERSION,
    classify_taxonomy,
    extract_taxonomy_facts,
)
from incidentpilot.evolution.candidate_generator import CandidateArtifact
from incidentpilot.evolution.registry import CandidateRegistry
from incidentpilot.incidents.progress import IncidentProgressRecorder
from incidentpilot.knowledge.retriever import RunbookRetriever
from incidentpilot.llm.gateway import (
    OpenAICompatibleChatTransport,
    StructuredOutputError,
    StructuredOutputGateway,
)
from incidentpilot.llm.profiles import ModelProfile
from incidentpilot.llm.structured_output import ModelInvocation, OutputStrategy
from incidentpilot.llm.usage import (
    ModelCallRecord,
    ModelCallRecorder,
    SqlAlchemyModelCallRecorder,
    estimate_cost_microusd,
)
from incidentpilot.mcp_servers.common.auth import CallerContext
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope
from incidentpilot.mcp_servers.telemetry.tools import TelemetryToolHandlers
from incidentpilot.observability.metrics import OperationalMetrics
from incidentpilot.observability.setup import (
    create_meter_provider,
    create_tracer_provider,
    instrument_httpx,
    instrument_sqlalchemy,
)
from incidentpilot.orchestration.prompts import VersionedPrompt, load_prompt_set
from incidentpilot.orchestration.state import (
    InvestigationBudget,
    InvestigationReport,
    RcaDiagnosisDraft,
    RcaSynthesisDraft,
    SynthesisDraft,
    TriageDecision,
)
from incidentpilot.remediation.online import OnlineRemediationCoordinator
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.unit_of_work import UnitOfWork
from incidentpilot.telemetry.backends.jaeger import JaegerBackend
from incidentpilot.telemetry.backends.opensearch import OpenSearchBackend
from incidentpilot.telemetry.backends.prometheus import PrometheusBackend
from incidentpilot.telemetry.query_registry import QueryRegistry
from incidentpilot.telemetry.schemas import LogSearch, MetricQuery, TraceSearch

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_API_DATABASE_URL = (
    "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
)
DEFAULT_WORKER_DATABASE_URL = (
    "postgresql+asyncpg://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"
)
DEFAULT_TELEMETRY_DATABASE_URL = (
    "postgresql+asyncpg://telemetry_mcp_role:telemetry-local-only@127.0.0.1:5433/incidentpilot"
)
DEFAULT_EVALUATION_DATABASE_URL = (
    "postgresql+asyncpg://evaluation_role:evaluation-local-only@127.0.0.1:5433/incidentpilot"
)
TELEMETRY_TOOL_VERSION = "telemetry-v9"
EVALUATION_LOG_SEVERITIES = ("DEBUG", "INFO", "WARN", "ERROR", "FATAL")
EVALUATION_METRIC_WINDOW_MINUTES = 2
EVALUATION_SYNTHESIS_VERSION = "v15-a1-t8-m1"
MIN_RECOVERY_OBSERVATION_SECONDS = 60
RECOVERY_RETRY_INTERVAL_SECONDS = 15
RECOVERY_RETRY_GRACE_SECONDS = 60
_RCA_PHASE_OVERRIDE = """
The current phase returns RcaSynthesisDraft. Do not assign root_cause_category; preserve the
grounded symptom, root cause, dependency, evidence, confidence, impact, and limits. A separate
bounded taxonomy step will classify a terminal RCA. Follow the supplied JSON Schema exactly.
""".strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic IncidentPilot evaluations")
    parser.add_argument("--mode", choices=("baseline", "multi"), required=True)
    parser.add_argument("--split", choices=("train", "validation"), required=True)
    parser.add_argument("--scenario")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--model-profile", choices=("fast", "strong"), default="fast")
    parser.add_argument(
        "--structured-output-strategy",
        choices=("tool_strategy", "json_output"),
        default="tool_strategy",
    )
    parser.add_argument("--no-actions", action="store_true")
    parser.add_argument("--candidate-id")
    return parser


def select_episodes(
    episodes: list[LoadedEpisode],
    *,
    split: str,
    scenario: str | None,
) -> list[LoadedEpisode]:
    selected = [episode for episode in episodes if episode.split == split]
    if scenario is not None:
        selected = [episode for episode in selected if episode.id == scenario]
        if not selected:
            raise ValueError(f"unknown scenario for {split}: {scenario}")
    if not selected:
        raise ValueError(f"no episodes found for split: {split}")
    return selected


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.no_actions:
        raise SystemExit("--no-actions is required until deterministic M7 action gates exist")
    episodes = select_episodes(
        load_episode_suite(ROOT / "scenarios", ROOT / "service_catalog" / "otel-demo.yaml"),
        split=args.split,
        scenario=args.scenario,
    )
    return run_evaluation(
        episodes=episodes,
        mode=cast(EvaluationMode, args.mode),
        split=args.split,
        seed=args.seed,
        model_profile=args.model_profile,
        structured_output_strategy=cast(OutputStrategy, args.structured_output_strategy),
        candidate_id=args.candidate_id,
    )


def run_evaluation(
    *,
    episodes: list[LoadedEpisode],
    mode: EvaluationMode,
    split: str,
    seed: int,
    model_profile: Literal["fast", "strong"],
    structured_output_strategy: OutputStrategy = "tool_strategy",
    candidate_id: str | None = None,
) -> int:
    llm_settings = LlmSettings()
    if llm_settings.selected_api_key is None:
        raise RuntimeError(
            "Configure INCIDENTPILOT_LLM_API_KEY or the current provider key in the ignored .env"
        )
    profile = build_evaluation_profile(llm_settings, ModelSettings(), model_profile)
    candidate = asyncio.run(_load_candidate(candidate_id)) if candidate_id is not None else None
    run_id = f"eval-{mode}-{datetime.now(UTC):%Y%m%d%H%M%S}-{seed}"
    suite_version = build_suite_version(split)
    candidate_version = build_candidate_version(
        profile,
        structured_output_strategy,
        query_digest=_query_template_digest(),
        prompt_digest=(
            candidate.id.removeprefix("candidate-")
            if candidate is not None
            else _prompt_set_digest()
        ),
        schema_version=EVALUATION_SYNTHESIS_VERSION,
        tool_version=TELEMETRY_TOOL_VERSION,
    )
    asyncio.run(
        _create_evaluation_run(
            run_id,
            suite_version=suite_version,
            candidate_version=candidate_version,
        )
    )
    cases: list[CaseScore] = []
    tracer_provider = create_tracer_provider("incidentpilot-evaluation")
    meter_provider = create_meter_provider("incidentpilot-evaluation")
    operational_metrics = OperationalMetrics(meter_provider)
    try:
        with httpx.Client(timeout=15, trust_env=False) as client:
            instrument_httpx(client, tracer_provider)
            controller = FlagdScenarioController(client=client)
            for index, episode in enumerate(episodes):
                case = _run_episode(
                    episode,
                    mode=mode,
                    seed=seed + index,
                    profile=profile,
                    llm_settings=llm_settings,
                    structured_output_strategy=structured_output_strategy,
                    controller=controller,
                    health_client=client,
                    tracer_provider=tracer_provider,
                    operational_metrics=operational_metrics,
                    candidate=candidate,
                )
                asyncio.run(_add_evaluation_case(run_id, case))
                cases.append(case)
                print(f"{episode.id}: {case.total:.3f}")
        aggregate = aggregate_run(mode=mode, cases=cases)
        asyncio.run(_complete_evaluation_run(run_id, aggregate))
        paths = write_mode_report(
            run_id=run_id,
            suite_version=suite_version,
            candidate_version=candidate_version,
            aggregate=aggregate,
            cases=cases,
            output_root=ROOT / "artifacts" / "evaluations",
        )
        print(f"run_id={run_id}")
        print(f"report={paths.markdown_path}")
        return 0
    except BaseException as exc:
        asyncio.run(_fail_evaluation_run(run_id, type(exc).__name__))
        raise
    finally:
        tracer_provider.shutdown()
        meter_provider.shutdown()


def _run_episode(
    episode: LoadedEpisode,
    *,
    mode: EvaluationMode,
    seed: int,
    profile: ModelProfile,
    llm_settings: LlmSettings,
    structured_output_strategy: OutputStrategy,
    controller: FlagdScenarioController,
    health_client: httpx.Client,
    tracer_provider: TracerProvider,
    operational_metrics: OperationalMetrics,
    candidate: CandidateArtifact | None,
) -> CaseScore:
    incident: dict[str, str] = {}
    observation_started_at = datetime.now(UTC)

    def send_alert(runtime_input: RuntimeEpisodeInput) -> str:
        incident_id = f"inc-eval-{uuid4().hex}"
        asyncio.run(_create_incident(incident_id, runtime_input, episode.id, mode, seed))
        incident["id"] = incident_id
        return incident_id

    def run_agent(runtime_input: RuntimeEpisodeInput, _: int) -> str:
        incident_id = incident["id"]
        asyncio.run(
            run_read_only_diagnosis(
                incident_id=incident_id,
                runtime_input=runtime_input,
                mode=mode,
                profile=profile,
                llm_settings=llm_settings,
                observation_started_at=observation_started_at,
                structured_output_strategy=structured_output_strategy,
                tracer_provider=tracer_provider,
                operational_metrics=operational_metrics,
                candidate=candidate,
            )
        )
        return incident_id

    runner = EpisodeRunner(
        controller=controller,
        preflight=lambda: EnvironmentMetadata(
            demo_tag="2.2.0",
            demo_commit="b74a7bc7bbe66099c61951f42b24dab8b6f02d18",
            prompt_version="v1",
            model_profile=profile.name,
            tool_version=TELEMETRY_TOOL_VERSION,
        ),
        capture_health=lambda: _health_snapshot(health_client),
        send_alert=send_alert,
        drive_traffic=lambda traffic: drive_otel_demo_traffic(health_client, traffic),
        run_agent=run_agent,
        score=lambda output, execution: {"incident_id": output},
        tracer_provider=tracer_provider,
        operational_metrics=operational_metrics,
    )
    result = runner.run(episode, seed=seed)
    recovery_passed = asyncio.run(_recovery_passed(episode))
    facts = asyncio.run(
        _load_evaluation_facts(
            episode=episode,
            incident_id=incident["id"],
            mode=mode,
            seed=seed,
            recovery_passed=recovery_passed,
            cleanup_succeeded=result.recovery.healthy,
        )
    )
    return score_case(
        facts=facts,
        execution=episode.execution,
        max_duration_seconds=episode.public_input.budgets.max_duration_seconds,
        max_read_tool_calls=episode.public_input.budgets.max_read_tool_calls,
        max_model_tokens=episode.public_input.budgets.max_model_tokens,
    )


async def _create_incident(
    incident_id: str,
    runtime_input: RuntimeEpisodeInput,
    scenario_id: str,
    mode: EvaluationMode,
    seed: int,
) -> None:
    database = Database(_database_url("API", DEFAULT_API_DATABASE_URL))
    try:
        async with UnitOfWork(database) as uow:
            alert = runtime_input.alert
            await uow.incidents.create_incident(
                incident_id=incident_id,
                tenant_id="local",
                alert=AlertPayload(
                    external_id=f"evaluation:{scenario_id}:{mode}:{seed}:{uuid4().hex}",
                    source="evaluation-runner",
                    title=alert.title,
                    description=alert.description,
                    severity=alert.severity,
                    starts_at=datetime.now(UTC),
                    service_hint=alert.service_hint,
                    labels=alert.labels,
                ),
            )
            await uow.commit()
    finally:
        await database.dispose()


async def _create_evaluation_run(
    run_id: str,
    *,
    suite_version: str,
    candidate_version: str,
) -> None:
    database = Database(_database_url("EVALUATION", DEFAULT_EVALUATION_DATABASE_URL))
    try:
        await EvaluationResultStore(database).create_run(
            run_id=run_id,
            suite_version=suite_version,
            candidate_version=candidate_version,
        )
    finally:
        await database.dispose()


async def _add_evaluation_case(run_id: str, score: CaseScore) -> None:
    database = Database(_database_url("EVALUATION", DEFAULT_EVALUATION_DATABASE_URL))
    try:
        await EvaluationResultStore(database).add_case(run_id=run_id, score=score)
    finally:
        await database.dispose()


async def _load_candidate(candidate_id: str) -> CandidateArtifact:
    database = Database(_database_url("EVALUATION", DEFAULT_EVALUATION_DATABASE_URL))
    try:
        return await CandidateRegistry(database).load_candidate(candidate_id)
    finally:
        await database.dispose()


async def _complete_evaluation_run(run_id: str, aggregate: RunAggregate) -> None:
    database = Database(_database_url("EVALUATION", DEFAULT_EVALUATION_DATABASE_URL))
    try:
        await EvaluationResultStore(database).complete_run(run_id=run_id, aggregate=aggregate)
    finally:
        await database.dispose()


async def _fail_evaluation_run(run_id: str, reason_code: str) -> None:
    database = Database(_database_url("EVALUATION", DEFAULT_EVALUATION_DATABASE_URL))
    try:
        await EvaluationResultStore(database).fail_run(
            run_id=run_id,
            reason_code=reason_code,
        )
    finally:
        await database.dispose()


async def _load_evaluation_facts(
    *,
    episode: LoadedEpisode,
    incident_id: str,
    mode: EvaluationMode,
    seed: int,
    recovery_passed: bool,
    cleanup_succeeded: bool,
) -> EvaluationFacts:
    database = Database(_database_url("EVALUATION", DEFAULT_EVALUATION_DATABASE_URL))
    try:
        return await EvaluationFactRepository(database).load(
            case_id=episode.id,
            incident_id=incident_id,
            mode=mode,
            seed=seed,
            recovery_passed=recovery_passed,
            cleanup_succeeded=cleanup_succeeded,
            trajectory_uri=f"report.json#{episode.id}",
            service_aliases=_service_aliases(),
        )
    finally:
        await database.dispose()


async def run_read_only_diagnosis(
    *,
    incident_id: str,
    runtime_input: RuntimeEpisodeInput,
    mode: EvaluationMode,
    profile: ModelProfile,
    llm_settings: LlmSettings,
    observation_started_at: datetime,
    structured_output_strategy: OutputStrategy,
    tracer_provider: TracerProvider,
    operational_metrics: OperationalMetrics,
    candidate: CandidateArtifact | None = None,
    worker_database_url: str | None = None,
    telemetry_database_url: str | None = None,
    production_runtime: bool = False,
) -> SynthesisDraft:
    telemetry_database = Database(
        telemetry_database_url or _database_url("TELEMETRY", DEFAULT_TELEMETRY_DATABASE_URL)
    )
    worker_database = Database(
        worker_database_url or _database_url("WORKER", DEFAULT_WORKER_DATABASE_URL)
    )
    client = httpx.AsyncClient(timeout=20, trust_env=False)
    instrument_sqlalchemy(telemetry_database.engine, tracer_provider)
    instrument_sqlalchemy(worker_database.engine, tracer_provider)
    instrument_httpx(client, tracer_provider)
    transport: OpenAICompatibleChatTransport | None = None
    progress = IncidentProgressRecorder(worker_database, incident_id=incident_id)
    try:
        run_started = time.perf_counter()
        await progress.emit(
            "run.started",
            stage="intake",
            status="running",
            message="已创建新的诊断运行，正在读取告警与服务目录",
            details={"mode": mode, "fresh_run": True},
        )
        await progress.set_incident_status(IncidentStatus.TRIAGING.value)
        catalog = _catalog()
        services = _scoped_services(runtime_input.alert.service_hint, catalog)
        registry = QueryRegistry.from_files(
            metrics_path=ROOT / "query_templates" / "metrics.yaml",
            logs_path=ROOT / "query_templates" / "logs.yaml",
            allowed_services=set(catalog),
        )
        transport = OpenAICompatibleChatTransport.from_settings(llm_settings)
        gateway = StructuredOutputGateway(
            transport=transport,
            recorder=CostingModelCallRecorder(
                SqlAlchemyModelCallRecorder(worker_database),
                profile=profile,
            ),
            tracer_provider=tracer_provider,
            operational_metrics=operational_metrics,
        )
        prompts = load_prompt_set(ROOT / "prompts" / "v1")
        if candidate is not None and candidate.kind != "prompt":
            raise ValueError("only prompt candidates can be evaluated by the current CLI")
        if candidate is not None and candidate.target_agent not in prompts.prompts:
            raise ValueError(f"candidate targets unknown agent: {candidate.target_agent}")

        def prompt_fields(agent_name: str) -> tuple[str, str]:
            prompt = prompts.prompts[agent_name]
            if candidate is not None and candidate.target_agent == agent_name:
                return candidate.id, candidate.proposed_content
            content = prompt.content
            if production_runtime:
                content += (
                    "\n\n## Display Language\n\n"
                    "All human-readable summaries, findings, impacts, limits, questions, and "
                    "reasons must use concise Simplified Chinese. Keep service names, Evidence "
                    "IDs, tool names, enum values, and schema keys unchanged."
                )
            return prompt.version, content

        alert_json = runtime_input.alert.model_dump(mode="json")
        handlers = TelemetryToolHandlers(
            database=telemetry_database,
            registry=registry,
            metrics=PrometheusBackend(
                client=client,
                registry=registry,
                base_url=os.environ.get("INCIDENTPILOT_PROMETHEUS_URL", "http://127.0.0.1:9090"),
            ),
            logs=OpenSearchBackend(
                client=client,
                base_url=os.environ.get("INCIDENTPILOT_OPENSEARCH_URL", "http://127.0.0.1:9200"),
            ),
            traces=JaegerBackend(
                client=client,
                base_url=os.environ.get("INCIDENTPILOT_JAEGER_URL", "http://127.0.0.1:16686"),
            ),
            runbooks=RunbookRetriever(telemetry_database),
            tracer_provider=tracer_provider,
            operational_metrics=operational_metrics,
        )
        end = datetime.now(UTC)
        start = observation_started_at
        callers = {
            name: CallerContext(
                tenant_id="local",
                incident_id=incident_id,
                subject=name,
                scopes=frozenset({"telemetry:read"}),
            )
            for name in ("baseline", "metrics", "logs", "traces", "runbook")
        }
        await progress.emit(
            "agent.started",
            stage="triage",
            status="running",
            message="分诊 Agent 正在确定影响范围与调查任务",
            agent="triage",
            details={"alert_service": runtime_input.alert.service_hint or "unknown"},
        )
        triage_started = time.perf_counter()
        selected_investigators = ["metrics", "logs", "traces"]
        if production_runtime:
            selected_investigators.append("runbook")
        if mode == "multi" and production_runtime:
            triage_version, triage_prompt = prompt_fields("triage")
            triage = await gateway.invoke(
                profile=profile,
                invocation=ModelInvocation(
                    incident_id=incident_id,
                    agent_name="triage",
                    prompt_version=triage_version,
                    system_prompt=triage_prompt,
                    user_prompt=json.dumps(
                        {
                            "alert": alert_json,
                            "allowed_services": services,
                            "service_dependencies": build_service_dependency_context(
                                services, catalog
                            ),
                            "available_investigators": selected_investigators,
                        },
                        ensure_ascii=False,
                    ),
                ),
                output_schema=TriageDecision,
                strategy=structured_output_strategy,
            )
            unknown_services = set(triage.scoped_services) - set(services)
            if unknown_services:
                raise ValueError(
                    f"triage selected an out-of-scope service: {sorted(unknown_services)[0]}"
                )
            services = triage.scoped_services
            selected_investigators = list(triage.investigators)
        await progress.emit(
            "agent.completed",
            stage="triage",
            status="completed",
            message=(
                f"已锁定 {len(services)} 个相关服务，并编排 "
                f"{len(selected_investigators)} 路并行调查"
            ),
            agent="triage",
            details={
                "services": services,
                "investigators": selected_investigators,
                "duration_ms": int((time.perf_counter() - triage_started) * 1000),
            },
        )
        await progress.set_incident_status(IncidentStatus.INVESTIGATING.value)
        tool_started = time.perf_counter()
        investigator_sources = {
            "metrics": ("metrics_investigator", "Prometheus 指标"),
            "logs": ("logs_investigator", "OpenSearch 日志"),
            "traces": ("traces_investigator", "Jaeger 调用链"),
            "runbook": ("runbook_analyst", "版本化处置手册"),
        }
        for signal in selected_investigators:
            agent, source = investigator_sources[signal]
            await progress.emit(
                "tool.started",
                stage="investigation",
                status="running",
                message=f"正在读取 {source}",
                agent=agent,
                details={"services": services, "source": source},
            )
        subject = "baseline" if mode == "baseline" else "metrics"
        tool_operations: dict[str, Any] = {}
        if "metrics" in selected_investigators:
            tool_operations["metrics"] = handlers.get_service_health_snapshot(
                callers[subject],
                services=services,
                window_minutes=EVALUATION_METRIC_WINDOW_MINUTES,
            )
        if "logs" in selected_investigators:
            tool_operations["logs"] = handlers.search_logs(
                callers["baseline" if mode == "baseline" else "logs"],
                LogSearch(
                    services=services,
                    severities=list(EVALUATION_LOG_SEVERITIES),
                    start=start,
                    end=end,
                    limit=20,
                ),
            )
        if "traces" in selected_investigators:
            tool_operations["traces"] = handlers.search_traces(
                callers["baseline" if mode == "baseline" else "traces"],
                TraceSearch(services=services, start=start, end=end, limit=20),
            )
        if "runbook" in selected_investigators:
            tool_operations["runbook"] = handlers.search_runbooks(
                callers["runbook"],
                query=f"{runtime_input.alert.title} {runtime_input.alert.description}",
                services=services[:10],
                limit=5,
            )
        selected_envelopes = await asyncio.gather(
            *(tool_operations[signal] for signal in selected_investigators)
        )
        envelopes = dict(zip(selected_investigators, selected_envelopes, strict=True))
        for signal in selected_investigators:
            agent = investigator_sources[signal][0]
            envelope = envelopes[signal]
            await progress.emit(
                "tool.completed",
                stage="investigation",
                status="completed" if envelope.ok else "failed",
                message=(
                    f"{signal.upper()} 已生成可审计 Evidence"
                    if envelope.ok
                    else f"{signal.upper()} 查询失败，已保留结构化错误"
                ),
                agent=agent,
                details={
                    "signal": signal,
                    "evidence_count": 1 if envelope.evidence_id else 0,
                    "evidence_id": envelope.evidence_id,
                    "tool_call_id": envelope.tool_call_id,
                    "duration_ms": int((time.perf_counter() - tool_started) * 1000),
                },
            )
        taxonomy_facts = extract_taxonomy_facts(envelopes)
        if mode == "baseline":
            invocation = ModelInvocation(
                incident_id=incident_id,
                agent_name="baseline",
                prompt_version="v1",
                system_prompt=(
                    "You are the single-agent read-only baseline. Use only the supplied alert and "
                    "Evidence envelopes. Return SynthesisDraft. Set diagnosis only when supported "
                    "by at least two existing Evidence IDs from distinct real-time signal kinds; "
                    "otherwise "
                    "leave diagnosis null. Never invent an Evidence ID or action."
                ),
                user_prompt=_prompt_payload(alert_json, services, envelopes),
            )
            draft = await gateway.invoke(
                profile=profile,
                invocation=invocation,
                output_schema=SynthesisDraft,
                strategy=structured_output_strategy,
            )
            reports: list[InvestigationReport] = []
        else:
            async def investigate(signal: str) -> InvestigationReport:
                agent_name = investigator_sources[signal][0]
                prompt_version, prompt_content = prompt_fields(agent_name)
                started = time.perf_counter()
                await progress.emit(
                    "agent.started",
                    stage="investigation",
                    status="running",
                    message=f"{agent_name} 正在分析刚刚取得的真实数据",
                    agent=agent_name,
                    details={"signal": signal, "evidence_id": envelopes[signal].evidence_id},
                )
                try:
                    report = await gateway.invoke(
                        profile=profile,
                        invocation=ModelInvocation(
                            incident_id=incident_id,
                            agent_name=agent_name,
                            prompt_version=prompt_version,
                            system_prompt=prompt_content,
                            user_prompt=_prompt_payload(
                                alert_json,
                                services,
                                {signal: envelopes[signal]},
                            ),
                        ),
                        output_schema=InvestigationReport,
                        strategy=structured_output_strategy,
                    )
                except StructuredOutputError:
                    if not production_runtime:
                        raise
                    report = InvestigationReport(
                        investigator=cast(
                            Literal["metrics", "logs", "traces", "runbook"], signal
                        ),
                        scope_services=services,
                        findings=[],
                        unanswered_questions=[
                            "模型未能返回合规的结构化报告；保留工具事实并转交指挥阶段。"
                        ],
                        tool_call_ids=[envelopes[signal].tool_call_id],
                    )
                await progress.emit(
                    "agent.completed",
                    stage="investigation",
                    status="completed",
                    message=(
                        report.findings[0].statement
                        if report.findings
                        else "未发现足以支持根因的异常，已保留为反证"
                    ),
                    agent=agent_name,
                    details={
                        "signal": signal,
                        "finding_count": len(report.findings),
                        "contradiction_count": len(report.contradictions),
                        "evidence_ids": list(
                            dict.fromkeys(
                                evidence_id
                                for finding in [*report.findings, *report.contradictions]
                                for evidence_id in finding.evidence_ids
                            )
                        ),
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                    },
                )
                return report

            reports = list(
                await asyncio.gather(
                    *(investigate(signal) for signal in selected_investigators)
                )
            )
            await progress.emit(
                "agent.started",
                stage="synthesis",
                status="running",
                message="事故指挥 Agent 正在交叉验证各路调查结果",
                agent="incident_commander",
                details={"report_count": len(reports)},
            )
            await progress.set_incident_status(IncidentStatus.SYNTHESIZING.value)
            synthesis_started = time.perf_counter()
            commander = prompts.prompts["incident_commander"]
            commander_version, commander_content = prompt_fields("incident_commander")
            try:
                draft = await synthesize_with_taxonomy(
                    gateway=gateway,
                    profile=profile,
                    incident_id=incident_id,
                    commander=VersionedPrompt(
                        metadata=commander.metadata,
                        content=commander_content,
                        digest=(candidate.digest if candidate is not None else commander.digest),
                    ),
                    prompt_version=commander_version,
                    commander_user_prompt=json.dumps(
                        {
                            "alert": alert_json,
                            "scoped_services": services,
                            "service_dependencies": build_service_dependency_context(
                                services, catalog
                            ),
                            "taxonomy_facts": taxonomy_facts.model_dump(mode="json"),
                            "evidence_alignment": build_evidence_alignment_context(envelopes),
                            "reports": [report.model_dump(mode="json") for report in reports],
                        },
                        ensure_ascii=False,
                    ),
                    taxonomy_envelopes=envelopes,
                    strategy=structured_output_strategy,
                    symptom_service=runtime_input.alert.service_hint,
                )
            except StructuredOutputError:
                if not production_runtime:
                    raise
                draft = SynthesisDraft(
                    reason="指挥 Agent 未返回合规结构；系统保持克制并转人工复核。"
                )
            if production_runtime and draft.diagnosis is not None:
                draft = draft.model_copy(
                    update={
                        "diagnosis": draft.diagnosis.model_copy(
                            update={
                                "root_cause_summary": (
                                    "实时错误证据将根因服务定位为 "
                                    f"{draft.diagnosis.root_cause_service}，并由至少两类遥测交叉支持。"
                                )
                            }
                        )
                    }
                )
            await progress.emit(
                "agent.completed",
                stage="synthesis",
                status="completed" if draft.diagnosis is not None else "waiting",
                message=(
                    draft.diagnosis.root_cause_summary
                    if draft.diagnosis is not None
                    else "现有证据不足以安全确认根因，已转人工复核"
                ),
                agent="incident_commander",
                details={
                    "root_cause_service": (
                        draft.diagnosis.root_cause_service
                        if draft.diagnosis is not None
                        else None
                    ),
                    "confidence": (
                        draft.diagnosis.confidence if draft.diagnosis is not None else None
                    ),
                    "evidence_ids": (
                        draft.diagnosis.evidence_ids if draft.diagnosis is not None else []
                    ),
                    "duration_ms": int((time.perf_counter() - synthesis_started) * 1000),
                },
            )
        evidence_ids = [
            envelope.evidence_id
            for envelope in envelopes.values()
            if envelope.evidence_id is not None
        ]
        tool_call_ids = [envelope.tool_call_id for envelope in envelopes.values()]
        execution_mode = ExecutionMode(
            runtime_input.alert.labels.get("execution_mode", ExecutionMode.READ_ONLY.value)
        )
        change_id = runtime_input.alert.labels.get("change_id")
        remediation_requested = (
            production_runtime
            and draft.diagnosis is not None
            and execution_mode is not ExecutionMode.READ_ONLY
            and bool(change_id)
        )
        await SqlAlchemyGraphResultSink(
            worker_database,
            model_profile=profile.name,
            prompt_version="v1",
        ).persist(
            incident_id,
            {
                "incident_id": incident_id,
                "status": (
                    IncidentStatus.DIAGNOSED.value
                    if remediation_requested
                    else IncidentStatus.RESOLVED_READ_ONLY.value
                    if draft.diagnosis is not None
                    else IncidentStatus.NEEDS_HUMAN.value
                ),
                "hypotheses": [item.model_dump(mode="json") for item in draft.hypotheses],
                "diagnosis": (
                    draft.diagnosis.model_dump(mode="json") if draft.diagnosis is not None else None
                ),
                "investigation_budget": InvestigationBudget(
                    wave=1,
                    max_waves=1,
                    read_calls_used=len(tool_call_ids),
                    max_read_calls=runtime_input.budgets.max_read_tool_calls,
                ).model_dump(mode="json"),
                "evidence_ids": evidence_ids,
                "tool_call_ids": tool_call_ids,
                "reports": [
                    {"wave": 1, "report": report.model_dump(mode="json")} for report in reports
                ],
            },
        )
        if remediation_requested and draft.diagnosis is not None and change_id is not None:
            verifying_key = os.environ.get("INCIDENTPILOT_APPROVAL_VERIFYING_KEY", "")
            if not verifying_key:
                raise RuntimeError("approval verifying key is required for remediation")
            await OnlineRemediationCoordinator(
                worker_database=worker_database,
                telemetry_database=telemetry_database,
                prometheus_url=os.environ.get(
                    "INCIDENTPILOT_PROMETHEUS_URL", "http://127.0.0.1:9090"
                ),
                action_mcp_url=os.environ.get(
                    "INCIDENTPILOT_ACTION_MCP_URL", "http://127.0.0.1:8102/mcp"
                ),
                approval_verifying_key=verifying_key,
            ).prepare(
                incident_id=incident_id,
                diagnosis=draft.diagnosis,
                change_id=change_id,
                execution_mode=execution_mode.value,
            )
        else:
            await progress.emit(
                "stage.completed",
                stage="safety",
                status="completed",
                message=(
                    "用户选择仅诊断：确定性安全门禁止所有写操作"
                    if execution_mode is ExecutionMode.READ_ONLY
                    else "当前事故没有可验证的 allowlist 动作，安全门保持只读"
                ),
                details={"decision": "read_only", "model_controls_policy": False},
            )
            await progress.emit(
                "stage.completed",
                stage="evolution",
                status="completed",
                message="本次运行已进入可审计样本池；是否生成改进候选由离线质量流程决定",
                details={"online_self_modification": False, "candidate_created": False},
            )
            await progress.emit(
                "incident.completed",
                stage="postmortem",
                status="completed",
                message="诊断报告与调查轨迹已持久化",
                details={"duration_ms": int((time.perf_counter() - run_started) * 1000)},
            )
        return draft
    except Exception as exc:
        await progress.emit(
            "run.failed",
            stage="postmortem",
            status="failed",
            message="诊断运行失败，错误已记录并可重试",
            details={"error_type": type(exc).__name__},
        )
        raise
    finally:
        if transport is not None:
            await transport.aclose()
        await client.aclose()
        await worker_database.dispose()
        await telemetry_database.dispose()


async def synthesize_with_taxonomy(
    *,
    gateway: StructuredOutputGateway,
    profile: ModelProfile,
    incident_id: str,
    commander: VersionedPrompt,
    prompt_version: str | None = None,
    commander_user_prompt: str,
    taxonomy_envelopes: dict[str, ToolEnvelope],
    strategy: OutputStrategy,
    symptom_service: str | None = None,
) -> SynthesisDraft:
    rca = await gateway.invoke(
        profile=profile,
        invocation=ModelInvocation(
            incident_id=incident_id,
            agent_name="incident_commander",
            prompt_version=prompt_version or commander.version,
            system_prompt=f"{commander.content}\n\n## RCA Phase Override\n\n{_RCA_PHASE_OVERRIDE}",
            user_prompt=commander_user_prompt,
        ),
        output_schema=RcaSynthesisDraft,
        strategy=strategy,
    )
    terminalized = False
    diagnosis = rca.diagnosis
    if diagnosis is None:
        terminal_diagnosis = _terminalize_eligible_hypothesis(
            rca.hypotheses,
            taxonomy_envelopes,
            symptom_service=symptom_service,
        )
        if terminal_diagnosis is None:
            return SynthesisDraft.model_validate(rca.model_dump(mode="json"))
        diagnosis = terminal_diagnosis
        terminalized = True

    evidence_ids = _bind_diagnosis_evidence(
        diagnosis,
        rca.hypotheses,
        taxonomy_envelopes,
    )
    if evidence_ids is None:
        return SynthesisDraft(
            hypotheses=rca.hypotheses,
            next_wave_tasks=rca.next_wave_tasks,
            reason=rca.reason or "Diagnosis lacks two grounded realtime Evidence references.",
        )
    evidence_ids = bind_correlated_log_evidence(evidence_ids, taxonomy_envelopes)

    normalized_diagnosis = _normalize_taxonomy_rca(diagnosis, taxonomy_envelopes)
    taxonomy_facts = extract_taxonomy_facts(
        taxonomy_envelopes,
        service=normalized_diagnosis.root_cause_service,
    )
    diagnosis_payload = normalized_diagnosis.model_dump(mode="json")
    diagnosis_payload["evidence_ids"] = evidence_ids
    diagnosis_payload["root_cause_category"] = classify_taxonomy(
        normalized_diagnosis,
        taxonomy_facts,
    )
    return SynthesisDraft(
        hypotheses=rca.hypotheses,
        diagnosis=Diagnosis.model_validate(diagnosis_payload),
        next_wave_tasks=rca.next_wave_tasks,
        reason=(
            "Deterministic terminalization of one eligible model hypothesis."
            if terminalized
            else rca.reason
        ),
    )


def _bind_diagnosis_evidence(
    diagnosis: RcaDiagnosisDraft,
    hypotheses: list[RootCauseHypothesis],
    envelopes: dict[str, ToolEnvelope],
) -> list[str] | None:
    available = {
        envelope.evidence_id: signal
        for signal, envelope in envelopes.items()
        if signal in {"metrics", "logs", "traces"} and envelope.ok and envelope.evidence_id
    }
    candidates = list(diagnosis.evidence_ids)
    candidates.extend(
        evidence_id
        for hypothesis in hypotheses
        if hypothesis.root_cause_service == diagnosis.root_cause_service
        for evidence_id in hypothesis.supporting_evidence_ids
    )
    metric_envelope = envelopes.get("metrics")
    if (
        metric_envelope is not None
        and metric_envelope.evidence_id is not None
        and isinstance(metric_envelope.data, dict)
        and isinstance(metric_envelope.data.get("snapshots"), dict)
        and diagnosis.root_cause_service in metric_envelope.data["snapshots"]
    ):
        candidates.append(metric_envelope.evidence_id)
    selected = list(dict.fromkeys(item for item in candidates if item in available))
    if len({available[item] for item in selected}) < 2:
        return None
    return selected


def _terminalize_eligible_hypothesis(
    hypotheses: list[RootCauseHypothesis],
    envelopes: dict[str, ToolEnvelope],
    *,
    symptom_service: str | None,
) -> RcaDiagnosisDraft | None:
    """Finalize exactly one already-grounded model hypothesis without inferring a root cause."""
    if symptom_service is None:
        return None
    catalog = _catalog()
    eligible: list[tuple[RootCauseHypothesis, list[str]]] = []
    for hypothesis in hypotheses:
        if (
            hypothesis.confidence < 0.75
            or hypothesis.contradicting_evidence_ids
            or hypothesis.root_cause_service not in catalog
        ):
            continue
        draft = RcaDiagnosisDraft(
            symptom_service=symptom_service,
            root_cause_service=hypothesis.root_cause_service,
            root_cause_summary=hypothesis.failure_mode,
            confidence=hypothesis.confidence,
            evidence_ids=hypothesis.supporting_evidence_ids,
            customer_impact=(
                f"Requests involving {symptom_service} are impacted by the evidence-supported "
                "service failure."
            ),
        )
        evidence_ids = _bind_diagnosis_evidence(draft, hypotheses, envelopes)
        if evidence_ids is not None:
            eligible.append((hypothesis, evidence_ids))
    if len(eligible) != 1:
        return None
    hypothesis, evidence_ids = eligible[0]
    return RcaDiagnosisDraft(
        symptom_service=symptom_service,
        root_cause_service=hypothesis.root_cause_service,
        root_cause_summary=hypothesis.failure_mode,
        confidence=hypothesis.confidence,
        evidence_ids=evidence_ids,
        customer_impact=(
            f"Requests involving {symptom_service} are impacted by the evidence-supported "
            "service failure."
        ),
        diagnosis_limits=[
            "Terminal diagnosis was deterministically completed from one eligible model hypothesis."
        ],
    )


def bind_correlated_log_evidence(
    evidence_ids: list[str],
    envelopes: dict[str, ToolEnvelope],
) -> list[str]:
    """Include log evidence only when it is linked to an already selected trace."""
    traces = envelopes.get("traces")
    logs = envelopes.get("logs")
    if (
        traces is None
        or logs is None
        or not traces.ok
        or not logs.ok
        or traces.evidence_id not in evidence_ids
        or logs.evidence_id is None
        or not isinstance(traces.data, dict)
        or not isinstance(logs.data, dict)
    ):
        return evidence_ids
    trace_ids = {
        trace_id
        for raw_trace in cast(list[Any], traces.data.get("traces", []))
        if isinstance(raw_trace, dict)
        if isinstance((trace := cast(dict[str, Any], raw_trace)).get("trace_id"), str)
        for trace_id in [trace["trace_id"]]
    }
    log_trace_ids = {
        trace_id
        for raw_record in cast(list[Any], logs.data.get("records", []))
        if isinstance(raw_record, dict)
        if isinstance((record := cast(dict[str, Any], raw_record)).get("trace_id"), str)
        for trace_id in [record["trace_id"]]
    }
    if trace_ids & log_trace_ids:
        return list(dict.fromkeys([*evidence_ids, logs.evidence_id]))
    return evidence_ids


def _normalize_taxonomy_rca(
    diagnosis: RcaDiagnosisDraft,
    envelopes: dict[str, ToolEnvelope],
) -> RcaDiagnosisDraft:
    """Constrain terminal RCA fields to observed rate-limit and catalog facts."""
    catalog = _catalog()
    force_application = False
    if diagnosis.dependency_service == diagnosis.root_cause_service:
        diagnosis = diagnosis.model_copy(update={"dependency_service": None})
    cache_owner = _cache_failure_service(envelopes)
    if cache_owner is not None:
        diagnosis = diagnosis.model_copy(
            update={"root_cause_service": cache_owner, "dependency_service": None}
        )
    rate_limited_caller = _rate_limited_span_service(envelopes)
    if rate_limited_caller is not None:
        caller_dependencies = {
            str(item) for item in catalog.get(rate_limited_caller, {}).get("dependencies", [])
        }
        if diagnosis.root_cause_service in caller_dependencies:
            diagnosis = diagnosis.model_copy(
                update={
                    "root_cause_service": rate_limited_caller,
                    "dependency_service": diagnosis.root_cause_service,
                }
            )

    direct_error_dependency = _direct_error_dependency_service(diagnosis, envelopes, catalog)
    if (
        cache_owner is None
        and diagnosis.root_cause_service == diagnosis.symptom_service
        and direct_error_dependency
    ):
        diagnosis = diagnosis.model_copy(
            update={"root_cause_service": direct_error_dependency, "dependency_service": None}
        )
        force_application = True

    symptom_dependencies = {
        str(item) for item in catalog.get(diagnosis.symptom_service, {}).get("dependencies", [])
    }
    if (
        diagnosis.root_cause_service != diagnosis.symptom_service
        and diagnosis.root_cause_service in symptom_dependencies
    ):
        dependency_service: str | None = None
    else:
        root_dependencies = {
            str(item)
            for item in catalog.get(diagnosis.root_cause_service, {}).get("dependencies", [])
        }
        dependency_service = (
            diagnosis.dependency_service
            if diagnosis.dependency_service in root_dependencies
            else None
        )
    return diagnosis.model_copy(
        update={
            "dependency_service": None if force_application else dependency_service,
            "root_cause_summary": (
                "Observed error evidence identifies "
                f"{diagnosis.root_cause_service} as the root-cause service."
            ),
        }
    )


def _rate_limited_span_service(envelopes: dict[str, ToolEnvelope]) -> str | None:
    traces = envelopes.get("traces")
    if traces is None or not traces.ok or not isinstance(traces.data, dict):
        return None
    raw_traces = traces.data.get("traces")
    if not isinstance(raw_traces, list):
        return None
    services: set[str] = set()
    for raw_trace in cast(list[Any], raw_traces):
        if not isinstance(raw_trace, dict):
            continue
        raw_spans = cast(dict[str, Any], raw_trace).get("error_spans")
        if not isinstance(raw_spans, list):
            continue
        for raw_span in cast(list[Any], raw_spans):
            if not isinstance(raw_span, dict):
                continue
            span = cast(dict[str, Any], raw_span)
            service = span.get("service")
            if span.get("failure_type") == "rate_limited" and isinstance(service, str):
                services.add(service)
    return next(iter(services)) if len(services) == 1 else None


def _cache_failure_service(envelopes: dict[str, ToolEnvelope]) -> str | None:
    traces = envelopes.get("traces")
    if traces is None or not traces.ok or not isinstance(traces.data, dict):
        return None
    raw_traces = traces.data.get("traces")
    if not isinstance(raw_traces, list):
        return None
    hits: set[str] = set()
    misses: set[str] = set()
    mixed_error_paths: set[str] = set()
    for raw_trace in cast(list[Any], raw_traces):
        if not isinstance(raw_trace, dict):
            continue
        trace = cast(dict[str, Any], raw_trace)
        path_values: dict[str, set[bool]] = {}
        for raw_observation in cast(list[Any], trace.get("observations", [])):
            if not isinstance(raw_observation, dict):
                continue
            observation = cast(dict[str, Any], raw_observation)
            service = observation.get("service")
            attributes = observation.get("attributes")
            if not isinstance(service, str) or not isinstance(attributes, dict):
                continue
            cache_hit = cast(dict[str, Any], attributes).get("app.cache_hit")
            if cache_hit is True and trace.get("error") is False:
                hits.add(service)
            if cache_hit is False and trace.get("error") is True:
                misses.add(service)
            if isinstance(cache_hit, bool):
                path_values.setdefault(service, set()).add(cache_hit)
        if trace.get("error") is True:
            error_services = {
                service
                for raw_span in cast(list[Any], trace.get("error_spans", []))
                if isinstance(raw_span, dict)
                if isinstance((span := cast(dict[str, Any], raw_span)).get("service"), str)
                for service in [cast(str, span["service"])]
            }
            mixed_error_paths.update(
                service
                for service, values in path_values.items()
                if values == {False, True} and service in error_services
            )
    candidates = (hits & misses) | mixed_error_paths
    return next(iter(candidates)) if len(candidates) == 1 else None


def _direct_error_dependency_service(
    diagnosis: RcaDiagnosisDraft,
    envelopes: dict[str, ToolEnvelope],
    catalog: dict[str, dict[str, Any]],
) -> str | None:
    direct_dependencies = {
        str(item) for item in catalog.get(diagnosis.symptom_service, {}).get("dependencies", [])
    }
    traces = envelopes.get("traces")
    if traces is None or not traces.ok or not isinstance(traces.data, dict):
        return None
    raw_traces = traces.data.get("traces")
    if not isinstance(raw_traces, list):
        return None
    observed: set[str] = set()
    for raw_trace in cast(list[Any], raw_traces):
        if not isinstance(raw_trace, dict):
            continue
        raw_spans = cast(dict[str, Any], raw_trace).get("error_spans")
        if not isinstance(raw_spans, list):
            continue
        for raw_span in cast(list[Any], raw_spans):
            if not isinstance(raw_span, dict):
                continue
            service = cast(dict[str, Any], raw_span).get("service")
            if isinstance(service, str):
                observed.add(service)
    candidates = direct_dependencies & observed
    return next(iter(candidates)) if len(candidates) == 1 else None


def _prompt_payload(
    alert: dict[str, Any],
    services: list[str],
    envelopes: dict[str, ToolEnvelope],
) -> str:
    value = json.dumps(
        {
            "alert": alert,
            "scoped_services": services,
            "evidence": {
                signal: envelope.model_dump(mode="json") for signal, envelope in envelopes.items()
            },
        },
        ensure_ascii=False,
    )
    return value[:40_000]


async def _recovery_passed(episode: LoadedEpisode) -> bool:
    schedule = recovery_observation_schedule(episode.execution.recovery.observation_seconds)
    await asyncio.sleep(schedule[0])
    catalog = _catalog()
    registry = QueryRegistry.from_files(
        metrics_path=ROOT / "query_templates" / "metrics.yaml",
        logs_path=ROOT / "query_templates" / "logs.yaml",
        allowed_services=set(catalog),
    )
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        backend = PrometheusBackend(client=client, registry=registry)
        previous_observation_seconds = schedule[0]
        for observation_seconds in schedule:
            if observation_seconds != previous_observation_seconds:
                await asyncio.sleep(observation_seconds - previous_observation_seconds)
            previous_observation_seconds = observation_seconds
            checks_passed = True
            for check in episode.execution.recovery.checks:
                result = await backend.query_range(
                    build_recovery_query(
                        check,
                        configured_seconds=episode.execution.recovery.observation_seconds,
                    )
                )
                values = [series.points[-1].value for series in result.series if series.points]
                if not values or not _compare(values[-1], check.comparator, check.threshold):
                    checks_passed = False
                    break
            if checks_passed:
                return True
    return False


def effective_recovery_observation_seconds(configured_seconds: int) -> int:
    """Avoid querying a partial OTel span-metrics rate window after cleanup."""
    return max(configured_seconds, MIN_RECOVERY_OBSERVATION_SECONDS)


def recovery_observation_schedule(configured_seconds: int) -> tuple[int, ...]:
    """Retry after bounded collector lag while preserving the configured SLO check."""
    first_observation_seconds = effective_recovery_observation_seconds(configured_seconds)
    last_observation_seconds = first_observation_seconds + RECOVERY_RETRY_GRACE_SECONDS
    return tuple(
        range(
            first_observation_seconds,
            last_observation_seconds + RECOVERY_RETRY_INTERVAL_SECONDS,
            RECOVERY_RETRY_INTERVAL_SECONDS,
        )
    )


def build_recovery_query(
    check: RecoveryCheck,
    *,
    configured_seconds: int,
) -> MetricQuery:
    end = datetime.now(UTC)
    window_seconds = effective_recovery_observation_seconds(configured_seconds)
    window = f"{window_seconds}s"
    return MetricQuery(
        template_id=check.template_id,
        service=check.service,
        start=end - timedelta(seconds=window_seconds),
        end=end,
        step_seconds=min(15, window_seconds),
        duration=window,
        window=window,
    )


def _compare(value: float, comparator: str, threshold: float | list[float]) -> bool:
    if comparator == "between":
        return (
            isinstance(threshold, list)
            and len(threshold) == 2
            and threshold[0] <= value <= threshold[1]
        )
    if isinstance(threshold, list):
        return False
    return {
        "lt": value < threshold,
        "lte": value <= threshold,
        "gt": value > threshold,
        "gte": value >= threshold,
    }[comparator]


def _health_snapshot(client: httpx.Client) -> HealthSnapshot:
    endpoints = {
        "storefront": "http://127.0.0.1:8080/",
        "prometheus": "http://127.0.0.1:9090/-/ready",
        "opensearch": "http://127.0.0.1:9200/_cluster/health",
        "jaeger": "http://127.0.0.1:16686/jaeger/ui/api/v3/services",
    }
    details: dict[str, Any] = {}
    for name, url in endpoints.items():
        try:
            details[name] = client.get(url).is_success
        except httpx.HTTPError:
            details[name] = False
    return HealthSnapshot(healthy=all(details.values()), details=details)


def drive_otel_demo_traffic(
    client: httpx.Client,
    traffic: TrafficSpec,
    *,
    base_url: str = "http://127.0.0.1:8080",
    settle_seconds: float = 3,
) -> None:
    time.sleep(settle_seconds)
    product_id = "0PUK6V6EV0"
    catalog_fault_product_id = "OLJCESPC7Z"
    assistant_product_id = "6E92ZMYYFZ"
    ads_failures = 0
    ai_assistant_failures = 0
    recommendation_failures = 0
    for _ in range(traffic.requests):
        if traffic.operation == "ads":
            response = client.get(f"{base_url}/api/data", params={"contextKeys": "ad"})
            ads_failures += response.status_code >= 500
            if ads_failures:
                break
            continue
        if traffic.operation == "ai_assistant":
            response = client.post(
                f"{base_url}/api/product-ask-ai-assistant/{assistant_product_id}",
                json={"question": "Can you summarize the product reviews?"},
            )
            ai_assistant_failures += (
                response.status_code >= 500
                or "unable to process your response" in response.text.lower()
            )
            if ai_assistant_failures:
                break
            continue
        if traffic.operation == "product_detail":
            response = client.get(f"{base_url}/api/products/{catalog_fault_product_id}")
            if response.status_code < 500:
                raise RuntimeError("product detail traffic did not exercise the configured fault")
            continue
        if traffic.operation == "recommendations":
            response = client.get(
                f"{base_url}/api/recommendations",
                params={"productIds": product_id},
            )
            recommendation_failures += response.status_code >= 500
            continue
        user_id = uuid4().hex
        if traffic.operation == "cart":
            response = client.request(
                "DELETE",
                f"{base_url}/api/cart",
                json={"userId": user_id},
            )
            if response.status_code < 500:
                raise RuntimeError("cart traffic did not exercise the configured fault")
            continue
        client.get(f"{base_url}/api/products/{product_id}").raise_for_status()
        client.post(
            f"{base_url}/api/cart",
            json={"item": {"productId": product_id, "quantity": 1}, "userId": user_id},
        ).raise_for_status()
        response = client.post(
            f"{base_url}/api/checkout",
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
        if response.status_code < 500:
            raise RuntimeError("checkout traffic did not exercise the configured fault")
    if traffic.operation == "ads" and ads_failures == 0:
        raise RuntimeError("ads traffic did not exercise the configured fault")
    if traffic.operation == "ai_assistant" and ai_assistant_failures == 0:
        raise RuntimeError("AI assistant traffic did not exercise the configured fault")
    if traffic.operation == "recommendations" and recommendation_failures == 0:
        raise RuntimeError("recommendation traffic did not exercise the configured fault")


def build_evaluation_profile(
    llm: LlmSettings,
    models: ModelSettings,
    name: Literal["fast", "strong"],
) -> ModelProfile:
    configured = models.fast if name == "fast" else models.strong
    model = configured or ("deepseek-v4-flash" if name == "fast" else "deepseek-v4-pro")
    return ModelProfile(
        name=name,
        provider=llm.provider,
        model=model,
        base_url=llm.base_url,
        temperature=0,
        max_tokens=4_000 if name == "fast" else 8_000,
        supports_tools=True,
        supports_native_schema=False if llm.provider == "deepseek" else None,
    )


def build_suite_version(split: str) -> str:
    if split == "train":
        return f"train-v3-score-{SCORER_VERSION}"
    if split == "validation":
        return f"validation-v2-score-{SCORER_VERSION}"
    raise ValueError(f"unsupported evaluation split: {split}")


def build_candidate_version(
    profile: ModelProfile,
    strategy: OutputStrategy,
    *,
    query_digest: str,
    prompt_digest: str,
    schema_version: str,
    tool_version: str,
) -> str:
    return (
        f"p1-{prompt_digest}:{profile.model}:{strategy}:q-{query_digest}:"
        f"t-{tool_version}:s-{schema_version}"
    )


def _prompt_set_digest() -> str:
    digest = sha256()
    prompt_set = load_prompt_set(ROOT / "prompts" / "v1")
    for name, prompt in sorted(prompt_set.prompts.items()):
        digest.update(name.encode())
        digest.update(prompt.digest.encode())
    digest.update(_RCA_PHASE_OVERRIDE.encode())
    digest.update(TAXONOMY_POLICY_VERSION.encode())
    return digest.hexdigest()[:12]


def _query_template_digest() -> str:
    digest = sha256()
    digest.update(b"episode-observation-window-v8-fixed-recovery-rate-window-metrics-all-severity-logs")
    for path in (
        ROOT / "query_templates" / "metrics.yaml",
        ROOT / "query_templates" / "logs.yaml",
    ):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


class CostingModelCallRecorder:
    def __init__(
        self,
        delegate: ModelCallRecorder,
        *,
        profile: ModelProfile,
    ) -> None:
        self._delegate = delegate
        self._profile = profile

    async def record(self, record: ModelCallRecord) -> None:
        await self._delegate.record(price_model_call(record, self._profile))


def price_model_call(
    record: ModelCallRecord,
    profile: ModelProfile,
) -> ModelCallRecord:
    if profile.model == "qwen3.7-flash":
        input_price, output_price = (
            (0.03, 0.13)
            if record.usage.input_tokens <= 32_000
            else (0.10, 0.40)
            if record.usage.input_tokens <= 256_000
            else (0.20, 0.80)
        )
    elif profile.model == "qwen3.7-plus":
        input_price, output_price = (
            (0.276, 1.101) if record.usage.input_tokens <= 256_000 else (0.826, 3.301)
        )
    else:
        input_price, output_price = {
            "deepseek-v4-flash": (0.14, 0.28),
            "deepseek-v4-pro": (0.435, 0.87),
            "qwen3.6-flash": (0.165, 0.99),
        }[profile.model]
    cost = estimate_cost_microusd(
        input_tokens=record.usage.input_tokens,
        output_tokens=record.usage.output_tokens,
        input_usd_per_million=input_price,
        output_usd_per_million=output_price,
    )
    return record.model_copy(
        update={"usage": record.usage.model_copy(update={"cost_microusd": cost})}
    )


def _catalog() -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load((ROOT / "service_catalog" / "otel-demo.yaml").read_text(encoding="utf-8"))
    services = cast(list[dict[str, Any]], raw["services"])
    return {str(service["name"]): service for service in services}


def _scoped_services(
    service_hint: str | None,
    catalog: dict[str, dict[str, Any]],
) -> list[str]:
    if service_hint is None or service_hint not in catalog:
        raise ValueError("evaluation alert requires a cataloged service_hint")
    dependencies = [str(item) for item in catalog[service_hint].get("dependencies", [])]
    return [service_hint, *dependencies][:10]


def build_service_dependency_context(
    services: list[str],
    catalog: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    scoped = set(services)
    return {
        service: [
            str(dependency)
            for dependency in catalog.get(service, {}).get("dependencies", [])
            if str(dependency) in scoped
        ]
        for service in services
    }


def build_evidence_alignment_context(
    envelopes: dict[str, ToolEnvelope],
) -> dict[str, Any]:
    """Summarize cross-signal correlation without interpreting unrelated success as recovery."""
    trace_envelope = envelopes.get("traces")
    trace_payload = (
        trace_envelope.data
        if trace_envelope and isinstance(trace_envelope.data, dict)
        else {}
    )
    raw_traces = trace_payload.get("traces")
    traces = cast(list[Any], raw_traces) if isinstance(raw_traces, list) else []
    error_trace_ids: set[str] = set()
    failing_operations: dict[tuple[str, str], int] = {}
    for raw_trace in traces:
        if not isinstance(raw_trace, dict):
            continue
        trace = cast(dict[str, Any], raw_trace)
        if trace.get("error") is not True:
            continue
        trace_id = trace.get("trace_id")
        if isinstance(trace_id, str):
            error_trace_ids.add(trace_id)
        raw_spans = trace.get("error_spans")
        spans = cast(list[Any], raw_spans) if isinstance(raw_spans, list) else []
        seen_in_trace: set[tuple[str, str]] = set()
        for raw_span in spans:
            if not isinstance(raw_span, dict):
                continue
            span = cast(dict[str, Any], raw_span)
            service = span.get("service")
            operation = span.get("operation")
            if isinstance(service, str) and isinstance(operation, str):
                seen_in_trace.add((service, operation))
        for key in seen_in_trace:
            failing_operations[key] = failing_operations.get(key, 0) + 1

    log_envelope = envelopes.get("logs")
    log_payload = (
        log_envelope.data
        if log_envelope and isinstance(log_envelope.data, dict)
        else {}
    )
    raw_records = log_payload.get("records")
    records = cast(list[Any], raw_records) if isinstance(raw_records, list) else []
    correlated = 0
    uncorrelated_success = 0
    for raw_record in records:
        if not isinstance(raw_record, dict):
            continue
        record = cast(dict[str, Any], raw_record)
        trace_id = record.get("trace_id")
        if isinstance(trace_id, str) and trace_id in error_trace_ids:
            correlated += 1
        elif record.get("severity") in {"DEBUG", "INFO"}:
            uncorrelated_success += 1

    return {
        "error_trace_ids": sorted(error_trace_ids),
        "failing_operations": [
            {"service": service, "operation": operation, "error_trace_count": count}
            for (service, operation), count in sorted(failing_operations.items())
        ],
        "log_alignment": {
            "record_count": len(records),
            "correlated_error_trace_records": correlated,
            "uncorrelated_success_records": uncorrelated_success,
        },
    }


def _service_aliases() -> dict[str, list[str]]:
    return {
        name: [str(alias) for alias in service.get("aliases", [])]
        for name, service in _catalog().items()
    }


def _database_url(role: str, default: str) -> str:
    return os.environ.get(f"INCIDENTPILOT_{role}_DATABASE_URL", default)
