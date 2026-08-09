from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from incidentpilot.auth.tokens import DevelopmentApprovalGrantVerifier
from incidentpilot.domain.actions import (
    ActionProposal,
    ActionResult,
    CompensationPlan,
    RollbackChangeAction,
    VerificationCheck,
)
from incidentpilot.domain.diagnosis import Diagnosis
from incidentpilot.domain.enums import EvidenceKind, IncidentStatus, RiskLevel
from incidentpilot.incidents.models import (
    ActionExecutionRow,
    ActionProposalRow,
    ApprovalRow,
    ChangeEventRow,
    EvidenceRow,
    IncidentRow,
    VerificationResultRow,
)
from incidentpilot.incidents.progress import IncidentProgressRecorder
from incidentpilot.remediation.action_mcp_client import (
    ApprovedActionMcpClient,
    StreamableHttpActionMcpTransport,
)
from incidentpilot.remediation.authorization_gate import (
    SqlAlchemyApprovalGrantReader,
    SqlAlchemyAuthorizationGate,
)
from incidentpilot.remediation.policy import ServerPolicyFacts, evaluate_pre_approval
from incidentpilot.remediation.verification import (
    PrometheusVerificationReader,
    PrometheusVerificationSampler,
    ProposalVerificationService,
    SqlAlchemyVerificationEvidenceRecorder,
)
from incidentpilot.remediation.workflow_store import SqlAlchemyRemediationWorkflowStore
from incidentpilot.runtime.database import Database
from incidentpilot.telemetry.backends.prometheus import PrometheusBackend
from incidentpilot.telemetry.query_registry import QueryRegistry

ROOT = Path(__file__).parents[3]


def build_rollback_proposal(
    *,
    incident_id: str,
    diagnosis: Diagnosis,
    change: ChangeEventRow,
    baseline: float,
) -> ActionProposal:
    check = VerificationCheck(
        service=change.service,
        metric="error_ratio",
        query_template_id="service_error_ratio",
        comparator="lt",
        threshold=0.05,
        observation_seconds=60,
    )
    return ActionProposal(
        action=RollbackChangeAction(
            target_service=change.service,
            change_id=change.id,
        ),
        risk=RiskLevel.MEDIUM,
        diagnosis_evidence_ids=diagnosis.evidence_ids,
        expected_effect=f"恢复 {change.service} 的变更前配置，并降低请求错误率。",
        compensation_plan=CompensationPlan(
            mode="automatic_snapshot_restore",
            trigger="partial_execution_failure",
            reason="Action Controller 持有动作前完整 flagd snapshot。",
            snapshot_ref=f"private://change/{change.id}",
        ),
        verification_checks=[check],
        verification_baseline={
            f"{change.service}:service_error_ratio:error_ratio": baseline
        },
        idempotency_key="rollback-" + hashlib.sha256(
            f"{incident_id}:{change.id}".encode()
        ).hexdigest()[:32],
    )


class OnlineRemediationCoordinator:
    def __init__(
        self,
        *,
        worker_database: Database,
        telemetry_database: Database,
        prometheus_url: str,
        action_mcp_url: str,
        approval_verifying_key: str,
    ) -> None:
        self._worker_database = worker_database
        self._telemetry_database = telemetry_database
        self._prometheus_url = prometheus_url
        self._action_mcp_url = action_mcp_url
        self._approval_verifying_key = approval_verifying_key.replace("\\n", "\n")

    async def prepare(
        self,
        *,
        incident_id: str,
        diagnosis: Diagnosis,
        change_id: str,
        execution_mode: str,
    ) -> str:
        async with self._worker_database.session_factory() as session:
            change = await session.get(ChangeEventRow, change_id)
        if change is None:
            raise LookupError("the public change event was not found")
        registry = QueryRegistry.from_files(
            metrics_path=ROOT / "query_templates" / "metrics.yaml",
            logs_path=ROOT / "query_templates" / "logs.yaml",
            allowed_services={change.service},
        )
        check = build_rollback_proposal(
            incident_id=incident_id,
            diagnosis=diagnosis,
            change=change,
            baseline=0.0,
        ).verification_checks[0]
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            sampler = PrometheusVerificationSampler(
                metrics=PrometheusBackend(
                    client=client,
                    registry=registry,
                    base_url=self._prometheus_url,
                )
            )
            baseline = await sampler.sample(check=check)
        proposal = build_rollback_proposal(
            incident_id=incident_id,
            diagnosis=diagnosis,
            change=change,
            baseline=baseline,
        )
        store = SqlAlchemyRemediationWorkflowStore(database=self._worker_database)
        proposal_id = await store.save_proposal(incident_id=incident_id, proposal=proposal)
        facts = await self._policy_facts(incident_id=incident_id, change=change)
        decision = evaluate_pre_approval(proposal, facts)
        await store.save_policy_decision(
            incident_id=incident_id,
            proposal_id=proposal_id,
            decision=decision,
        )
        progress = IncidentProgressRecorder(self._worker_database, incident_id=incident_id)
        await progress.emit(
            "approval.requested" if decision.allowed else "stage.completed",
            stage="safety",
            status="waiting" if decision.allowed else "failed",
            message=(
                "确定性安全门要求人工审阅这项中风险回滚"
                if decision.allowed
                else "处置建议未通过确定性安全门，系统不会执行"
            ),
            details={
                "proposal_id": proposal_id,
                "execution_mode": execution_mode,
                "risk": decision.assigned_risk.value,
                "reason_codes": decision.reason_codes,
                "model_controls_policy": False,
            },
        )
        return proposal_id

    async def resume(self, *, incident_id: str, approval_id: str) -> None:
        async with self._worker_database.session_factory() as session:
            record = (
                await session.execute(
                    select(ActionProposalRow, ApprovalRow)
                    .join(ApprovalRow, ApprovalRow.proposal_id == ActionProposalRow.id)
                    .where(
                        ActionProposalRow.incident_id == incident_id,
                        ApprovalRow.id == approval_id,
                    )
                )
            ).one_or_none()
        if record is None:
            raise LookupError("approved proposal was not found")
        proposal_row, _approval = record
        proposal = ActionProposal.model_validate(proposal_row.payload_json)
        progress = IncidentProgressRecorder(self._worker_database, incident_id=incident_id)
        result = await self._successful_execution(proposal_row.id)
        if result is None:
            verifier = DevelopmentApprovalGrantVerifier(
                issuer="https://incidentpilot.local",
                audience="action-mcp",
                public_key=self._approval_verifying_key,
            )
            await progress.emit(
                "stage.completed",
                stage="authorization",
                status="running",
                message="正在校验审批签名、提案摘要、权限范围与单次 nonce",
                details={"proposal_id": proposal_row.id, "approval_id": approval_id},
            )
            await SqlAlchemyAuthorizationGate(
                database=self._worker_database,
                grants=verifier,
            ).authorize(
                incident_id=incident_id,
                proposal_id=proposal_row.id,
                approval_id=approval_id,
            )
            action = ApprovedActionMcpClient(
                transport=StreamableHttpActionMcpTransport(endpoint=self._action_mcp_url),
                grants=SqlAlchemyApprovalGrantReader(database=self._worker_database),
            )
            result = await action.execute(
                incident_id=incident_id,
                proposal_id=proposal_row.id,
                proposal=proposal,
                approval_id=approval_id,
            )
        else:
            await progress.emit(
                "stage.completed",
                stage="authorization",
                status="completed",
                message="发现已持久化的成功动作，跳过重复授权与重复执行",
                details={
                    "proposal_id": proposal_row.id,
                    "approval_id": approval_id,
                    "execution_id": result.execution_id,
                    "resumed_idempotently": True,
                },
            )
        await progress.set_incident_status(
            IncidentStatus.EXECUTING.value
            if result.status in {"succeeded", "already_applied"}
            else IncidentStatus.ACTION_FAILED.value
        )
        await progress.emit(
            "action.completed",
            stage="execution",
            status="completed" if result.status in {"succeeded", "already_applied"} else "failed",
            message=(
                "Action MCP 已执行受限回滚，正在等待恢复观测窗口"
                if result.status in {"succeeded", "already_applied"}
                else "受限动作执行失败，已停止并转人工处理"
            ),
            details={
                "proposal_id": proposal_row.id,
                "execution_id": result.execution_id,
                "action_type": proposal.action.action_type,
                "idempotent_status": result.status,
            },
        )
        await progress.emit(
            "stage.completed",
            stage="safety",
            status="completed" if result.status in {"succeeded", "already_applied"} else "failed",
            message=(
                "审批、策略、权限与一次性授权均已通过确定性校验"
                if result.status in {"succeeded", "already_applied"}
                else "确定性执行边界已停止后续自动动作"
            ),
            details={
                "proposal_id": proposal_row.id,
                "execution_id": result.execution_id,
                "model_controls_policy": False,
            },
        )
        if result.status == "failed":
            return
        await progress.set_incident_status(IncidentStatus.VERIFYING.value)
        registry = QueryRegistry.from_files(
            metrics_path=ROOT / "query_templates" / "metrics.yaml",
            logs_path=ROOT / "query_templates" / "logs.yaml",
            allowed_services={proposal.action.target_service},
        )
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            sampler = PrometheusVerificationSampler(
                metrics=PrometheusBackend(
                    client=client,
                    registry=registry,
                    base_url=self._prometheus_url,
                )
            )
            verification = await ProposalVerificationService(
                reader=PrometheusVerificationReader(
                    sampler=sampler,
                    evidence=SqlAlchemyVerificationEvidenceRecorder(
                        database=self._telemetry_database
                    ),
                ),
                wait=asyncio.sleep,
            ).verify(incident_id=incident_id, proposal=proposal)
        async with self._worker_database.session_factory() as session, session.begin():
            await session.execute(
                insert(VerificationResultRow)
                .values(
                    id="verify_" + hashlib.sha256(result.execution_id.encode()).hexdigest()[:32],
                    execution_id=result.execution_id,
                    payload_json=verification.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=[VerificationResultRow.id])
            )
        terminal = IncidentStatus.RESOLVED if verification.recovered else IncidentStatus.NEEDS_HUMAN
        await progress.emit(
            "verification.completed",
            stage="verification",
            status="completed" if verification.recovered else "failed",
            message=(
                "真实 Prometheus 恢复指标通过，事故已关闭"
                if verification.recovered
                else "恢复指标未通过，系统已停止自动动作并转人工处理"
            ),
            details={
                "proposal_id": proposal_row.id,
                "execution_id": result.execution_id,
                "recovered": verification.recovered,
                "evidence_ids": verification.evidence_ids,
                "baseline": verification.baseline,
                "observed": verification.observed,
            },
        )
        await progress.emit(
            "stage.completed",
            stage="evolution",
            status="completed",
            message="本次诊断、审批、执行与恢复轨迹已进入受控离线样本池",
            details={"online_self_modification": False, "candidate_created": False},
        )
        await progress.set_incident_status(terminal.value)
        await progress.emit(
            "incident.completed",
            stage="postmortem",
            status="completed",
            message="事故处置闭环已持久化，可审计记录完整",
            details={"final_status": terminal.value},
        )

    async def _successful_execution(self, proposal_id: str) -> ActionResult | None:
        async with self._worker_database.session_factory() as session:
            execution = await session.scalar(
                select(ActionExecutionRow)
                .where(
                    ActionExecutionRow.proposal_id == proposal_id,
                    ActionExecutionRow.status == "succeeded",
                )
                .order_by(ActionExecutionRow.started_at.desc())
                .limit(1)
            )
        if execution is None:
            return None
        reference = execution.result_json.get("reference")
        return ActionResult(
            proposal_id=proposal_id,
            execution_id=execution.id,
            status="already_applied",
            started_at=execution.started_at,
            finished_at=execution.finished_at or execution.started_at,
            external_reference=reference if isinstance(reference, str) else None,
            sanitized_output=execution.result_json,
        )

    async def _policy_facts(
        self,
        *,
        incident_id: str,
        change: ChangeEventRow,
    ) -> ServerPolicyFacts:
        async with self._worker_database.session_factory() as session:
            incident = await session.get(IncidentRow, incident_id)
            evidence = (
                await session.scalars(
                    select(EvidenceRow).where(EvidenceRow.incident_id == incident_id)
                )
            ).all()
        if incident is None:
            raise LookupError("incident was not found")
        return ServerPolicyFacts(
            incident_status=IncidentStatus(incident.status),
            actor_role="operator",
            known_evidence_ids={item.id for item in evidence},
            available_realtime_evidence_kinds={EvidenceKind(item.kind) for item in evidence},
            restart_allowlist=set(),
            change_services={change.id: change.service},
            verification_template_ids={"service_error_ratio"},
        )
