from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TenantRow(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)


class ActorRow(Base):
    __tablename__ = "actors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32))


class IncidentRow(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source", "external_id", name="uq_incident_source"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    source: Mapped[str] = mapped_column(String(100))
    external_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AlertRow(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvidenceRow(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint(
            "incident_id",
            "kind",
            "digest",
            name="uq_evidence_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    kind: Mapped[str] = mapped_column(String(32))
    source_system: Mapped[str] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(Text)
    query_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    raw_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB, nullable=True)
    digest: Mapped[str] = mapped_column(String(64))
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HypothesisRow(Base):
    __tablename__ = "hypotheses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    wave: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)


class DiagnosisRow(Base):
    __tablename__ = "diagnoses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model_profile: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(100))


class ActionProposalRow(Base):
    __tablename__ = "action_proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32))
    policy_result_json: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("action_proposals.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("actors.id"))
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grant_jws: Mapped[str | None] = mapped_column(Text, nullable=True)
    grant_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    nonce_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionExecutionRow(Base):
    __tablename__ = "action_executions"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_action_idempotency"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("action_proposals.id"))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB)


class VerificationResultRow(Base):
    __tablename__ = "verification_results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("action_executions.id"))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(100))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64))


class AnalysisJobRow(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    job_type: Mapped[str] = mapped_column(String(16))
    resume_reference_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ServiceHeartbeatRow(Base):
    __tablename__ = "service_heartbeats"

    process_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ToolCallRow(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    agent_name: Mapped[str] = mapped_column(String(100))
    tool_name: Mapped[str] = mapped_column(String(100))
    args_digest: Mapped[str] = mapped_column(String(64))
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))


class ModelCallRow(Base):
    __tablename__ = "model_calls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    agent_name: Mapped[str] = mapped_column(String(100))
    model_profile: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_microusd: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))


class PromptVersionRow(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        Index(
            "ux_prompt_versions_one_active",
            "agent_name",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(100))
    content_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))


class RunbookVersionRow(Base):
    __tablename__ = "runbook_versions"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    version: Mapped[str] = mapped_column(String(100), primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB)


class RunbookSectionRow(Base):
    __tablename__ = "runbook_sections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["runbook_id", "version"],
            ["runbook_versions.id", "runbook_versions.version"],
        ),
        Index("ix_runbook_sections_search", "search_vector", postgresql_using="gin"),
    )

    runbook_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    version: Mapped[str] = mapped_column(String(100), primary_key=True)
    section_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    parent_title: Mapped[str | None] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64))
    services: Mapped[list[str]] = mapped_column(JSONB)
    symptoms: Mapped[list[str]] = mapped_column(JSONB)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english'::regconfig, "
            "coalesce(title, '') || ' ' || coalesce(content, '') || ' ' || "
            "coalesce(services::text, '') || ' ' || coalesce(symptoms::text, ''))",
            persisted=True,
        ),
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector())


class ChangeEventRow(Base):
    __tablename__ = "change_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    service: Mapped[str] = mapped_column(String(100))
    change_type: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChangeEventPrivateMappingRow(Base):
    __tablename__ = "change_event_private_mappings"

    change_id: Mapped[str] = mapped_column(ForeignKey("change_events.id"), primary_key=True)
    mapping_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    config_digest: Mapped[str] = mapped_column(String(64))


class EvaluationRunRow(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    suite_version: Mapped[str] = mapped_column(String(100))
    candidate_version: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32))
    aggregate_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)


class EvaluationCaseRow(Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (
        UniqueConstraint("run_id", "scenario_id", name="uq_evaluation_case_scenario"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"))
    scenario_id: Mapped[str] = mapped_column(String(200))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    hard_failures: Mapped[list[str]] = mapped_column(JSONB)


class EvolutionTrajectoryRow(Base):
    __tablename__ = "evolution_trajectories"
    __table_args__ = (UniqueConstraint("digest", name="uq_evolution_trajectory_digest"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"))
    scenario_id: Mapped[str] = mapped_column(String(200))
    split: Mapped[str] = mapped_column(String(16))
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    quality_reasons: Mapped[list[str]] = mapped_column(JSONB)
    content_digest: Mapped[str] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class CandidateVersionRow(Base):
    __tablename__ = "candidate_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    base_version: Mapped[str] = mapped_column(String(100))
    artifact_uri: Mapped[str] = mapped_column(String(300))
    artifact_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    diff: Mapped[str] = mapped_column(Text)
    target_failure_label: Mapped[str] = mapped_column(String(64))
    target_component: Mapped[str] = mapped_column(String(100))
    generator_model: Mapped[str] = mapped_column(String(100))
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class PromotionCycleRow(Base):
    __tablename__ = "promotion_cycles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidate_versions.id"))
    candidate_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    holdout_suite_digest: Mapped[str | None] = mapped_column(String(64))
    holdout_passed: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class PromotionGateRecordRow(Base):
    __tablename__ = "promotion_gate_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidate_versions.id"))
    cycle_id: Mapped[str | None] = mapped_column(ForeignKey("promotion_cycles.id"))
    status: Mapped[str] = mapped_column(String(32))
    decision_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    human_rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
