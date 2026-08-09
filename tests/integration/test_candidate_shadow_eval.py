from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from incidentpilot.evolution.promotion_gate import PromotionDecision, PromotionGateStore
from incidentpilot.runtime.database import Database

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)


@pytest.mark.integration
async def test_staging_cycle_locks_one_candidate_and_retains_human_rejection() -> None:
    database = Database(MIGRATION_URL)
    candidate_id = f"candidate-{uuid4().hex[:12]}"
    cycle_id = f"cycle-{uuid4().hex}"
    store = PromotionGateStore(database)
    decision = PromotionDecision(
        candidate_id=candidate_id,
        recommendation="staging",
        checks=[],
        validation_score_delta=0.04,
        validation_cost_reduction=0.10,
        validation_root_cause_delta=0.0,
        worst_validation_score_delta=0.04,
    )
    try:
        await _insert_candidate(database, candidate_id)
        await store.freeze_staging_cycle(
            cycle_id=cycle_id,
            candidate_digest="d" * 64,
            decision=decision,
        )
        with pytest.raises(RuntimeError, match="already frozen"):
            await store.freeze_staging_cycle(
                cycle_id=cycle_id,
                candidate_digest="d" * 64,
                decision=decision,
            )
        await store.record_human_rejection(
            candidate_id=candidate_id,
            reason="Operator rejected the draft after diff review.",
        )
        async with database.engine.connect() as connection:
            cycle_status = await connection.scalar(
                text("SELECT status FROM promotion_cycles WHERE id = :id"), {"id": cycle_id}
            )
            rejection = await connection.scalar(
                text(
                    "SELECT human_rejection_reason FROM promotion_gate_records "
                    "WHERE candidate_id = :candidate_id ORDER BY created_at DESC LIMIT 1"
                ),
                {"candidate_id": candidate_id},
            )
        assert cycle_status == "staging_frozen"
        assert rejection == "Operator rejected the draft after diff review."
    finally:
        await database.dispose()


async def _insert_candidate(database: Database, candidate_id: str) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO candidate_versions "
                "(id, kind, base_version, artifact_uri, artifact_json, diff, "
                "target_failure_label, target_component, generator_model, digest, status) "
                "VALUES (:id, 'prompt', 'v1', :uri, CAST(:artifact AS jsonb), 'diff', "
                "'unsupported_claim', 'synthesizer', 'test', :digest, 'candidate')"
            ),
            {
                "id": candidate_id,
                "uri": f"evolution://candidates/{candidate_id}",
                "artifact": "{}",
                "digest": uuid4().hex + uuid4().hex,
            },
        )
