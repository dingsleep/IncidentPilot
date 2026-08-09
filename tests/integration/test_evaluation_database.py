from __future__ import annotations

import json
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from incidentpilot.evaluation.loader import ExecutionSpec
from incidentpilot.evaluation.metrics import EvaluationFactRepository, EvaluationResultStore
from incidentpilot.evaluation.scorer import score_case
from incidentpilot.runtime.database import Database
from incidentpilot.telemetry.normalization import canonical_digest

MIGRATION_URL = (
    "postgresql+psycopg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
EVALUATION_URL = (
    "postgresql+asyncpg://evaluation_role:evaluation-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
WORKER_URL = "postgresql+asyncpg://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"


@pytest.mark.integration
async def test_evaluation_tables_are_writable_only_by_the_evaluation_role() -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", MIGRATION_URL)
    command.upgrade(config, "head")
    run_id = f"eval-{uuid4().hex}"
    case_id = f"eval-case-{uuid4().hex}"
    evaluation = Database(EVALUATION_URL)
    api = Database(API_URL)
    worker = Database(WORKER_URL)
    try:
        async with evaluation.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO evaluation_runs "
                    "(id, suite_version, candidate_version, status, aggregate_metrics) "
                    "VALUES (:id, :suite, :candidate, :status, CAST(:metrics AS jsonb))"
                ),
                {
                    "id": run_id,
                    "suite": "validation-v1",
                    "candidate": "active-v1",
                    "status": "running",
                    "metrics": json.dumps({}),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO evaluation_cases "
                    "(id, run_id, scenario_id, metrics, hard_failures) "
                    "VALUES (:id, :run_id, :scenario, CAST(:metrics AS jsonb), "
                    "CAST(:failures AS jsonb))"
                ),
                {
                    "id": case_id,
                    "run_id": run_id,
                    "scenario": "payment-failure-001",
                    "metrics": json.dumps({"score": 0.8}),
                    "failures": json.dumps([]),
                },
            )

        async with api.engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM evaluation_runs WHERE id = :id"), {"id": run_id}
                )
                == 1
            )

        async with evaluation.engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM diagnoses")) is not None
            assert await connection.scalar(text("SELECT count(*) FROM tool_calls")) is not None
            assert await connection.scalar(text("SELECT count(*) FROM model_calls")) is not None

        with pytest.raises(DBAPIError, match="permission denied"):
            async with evaluation.engine.begin() as connection:
                await connection.execute(
                    text("UPDATE diagnoses SET model_profile = 'forbidden-evaluation-write'")
                )

        with pytest.raises(DBAPIError, match="permission denied"):
            async with worker.engine.connect() as connection:
                await connection.scalar(text("SELECT count(*) FROM evaluation_runs"))
    finally:
        await worker.dispose()
        await api.dispose()
        await evaluation.dispose()


@pytest.mark.integration
async def test_evaluation_score_is_loaded_from_and_persisted_with_database_facts() -> None:
    run_id = f"eval-{uuid4().hex}"
    incident_id = f"inc-eval-{uuid4().hex}"
    evidence_ids = [f"ev-{uuid4().hex}", f"ev-{uuid4().hex}"]
    diagnosis_id = f"diag-{uuid4().hex}"
    tool_id = f"tool-{uuid4().hex}"
    model_id = f"model-{uuid4().hex}"
    raw_values = [
        {"service": "payment", "error_rate": 6},
        {"service": "checkout", "dependency": "payment", "errors": 6},
    ]
    diagnosis = {
        "symptom_service": "checkout",
        "root_cause_service": "payment",
        "dependency_service": "payment",
        "root_cause_category": "dependency_unavailable",
        "root_cause_summary": "payment caused 6 checkout errors",
        "confidence": 0.9,
        "evidence_ids": evidence_ids,
        "alternatives": [],
        "customer_impact": "checkout failed",
        "diagnosis_limits": [],
    }
    execution = ExecutionSpec.model_validate(
        {
            "control_type": "fault",
            "injections": [
                {
                    "adapter": "flagd",
                    "operation": "enable",
                    "service": "payment",
                    "scenario_key": "opaque-test-key",
                    "variant": "on",
                    "warmup_seconds": 0,
                }
            ],
            "ground_truth": {
                "root_cause_service": "payment",
                "dependency_service": "checkout",
                "category": "dependency_unavailable",
                "required_signal_kinds": ["metric", "trace"],
            },
            "allowed_actions": [],
            "recovery": {
                "observation_seconds": 30,
                "checks": [
                    {
                        "template_id": "checkout-success",
                        "service": "checkout",
                        "comparator": "gte",
                        "threshold": 1,
                    }
                ],
            },
            "cleanup": [{"adapter": "flagd", "operation": "restore_snapshot"}],
        }
    )
    migration = Database(MIGRATION_URL.replace("+psycopg", "+asyncpg"))
    evaluation = Database(EVALUATION_URL)
    try:
        async with migration.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO incidents "
                    "(id, tenant_id, source, external_id, status, severity, title) "
                    "VALUES (:id, 'local', 'evaluation-test', :id, 'DIAGNOSED', 'P2', "
                    "'Evaluation fact trace')"
                ),
                {"id": incident_id},
            )
            for evidence_id, kind, raw in zip(
                evidence_ids, ("metric", "trace"), raw_values, strict=True
            ):
                await connection.execute(
                    text(
                        "INSERT INTO evidence "
                        "(id, incident_id, kind, source_system, summary, query_json, raw_json, "
                        "digest, observed_start, observed_end, truncated, collected_at) VALUES "
                        "(:id, :incident_id, :kind, 'evaluation-test', :summary, "
                        "CAST('{}' AS jsonb), "
                        "CAST(:raw AS jsonb), :digest, now(), now(), false, now())"
                    ),
                    {
                        "id": evidence_id,
                        "incident_id": incident_id,
                        "kind": kind,
                        "summary": "payment and checkout show 6 errors",
                        "raw": json.dumps(raw),
                        "digest": canonical_digest(raw),
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO diagnoses "
                    "(id, incident_id, payload_json, model_profile, prompt_version) VALUES "
                    "(:id, :incident_id, CAST(:payload AS jsonb), 'test', 'v1')"
                ),
                {
                    "id": diagnosis_id,
                    "incident_id": incident_id,
                    "payload": json.dumps(diagnosis),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO tool_calls "
                    "(id, incident_id, agent_name, tool_name, args_digest, result_digest, "
                    "duration_ms, status) VALUES "
                    "(:id, :incident_id, 'metrics', 'get_service_health_snapshot', "
                    ":digest, :digest, 20, 'SUCCESS')"
                ),
                {"id": tool_id, "incident_id": incident_id, "digest": "a" * 64},
            )
            await connection.execute(
                text(
                    "INSERT INTO model_calls "
                    "(id, incident_id, agent_name, model_profile, prompt_version, input_tokens, "
                    "output_tokens, cost_microusd, duration_ms, status) VALUES "
                    "(:id, :incident_id, 'synthesis', 'test', 'v1', 100, 20, 3, 40, 'SUCCESS')"
                ),
                {"id": model_id, "incident_id": incident_id},
            )

        facts = await EvaluationFactRepository(evaluation).load(
            case_id="payment-failure-db-facts",
            incident_id=incident_id,
            mode="multi",
            seed=7,
            recovery_passed=True,
            cleanup_succeeded=True,
        )
        case = score_case(
            facts=facts,
            execution=execution,
            max_duration_seconds=300,
            max_read_tool_calls=8,
            max_model_tokens=4_000,
        )
        await EvaluationResultStore(evaluation).create_run(
            run_id=run_id,
            suite_version="validation-v1",
            candidate_version="test-v1",
        )
        await EvaluationResultStore(evaluation).add_case(run_id=run_id, score=case)

        assert case.root_cause.fact_ids == [diagnosis_id]
        assert set(case.evidence_fidelity.fact_ids) == set(evidence_ids)
        assert tool_id in case.tool_process.fact_ids
        assert model_id in case.efficiency.fact_ids
        async with evaluation.engine.connect() as connection:
            stored = await connection.scalar(
                text("SELECT metrics FROM evaluation_cases WHERE run_id = :run_id"),
                {"run_id": run_id},
            )
        assert stored["facts_digest"] == case.facts_digest
    finally:
        await evaluation.dispose()
        await migration.dispose()
