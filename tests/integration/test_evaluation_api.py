from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from incidentpilot.api.main import create_app
from incidentpilot.config import ApiSettings, Settings
from incidentpilot.runtime.database import Database

API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
EVALUATION_URL = (
    "postgresql+asyncpg://evaluation_role:evaluation-local-only@127.0.0.1:5433/incidentpilot"
)
VIEWER = {"X-IncidentPilot-Actor": "local-viewer"}


def _app():
    return create_app(
        Settings(
            environment="development",
            api=ApiSettings(database_url=SecretStr(API_URL)),
        )
    )


@pytest.mark.integration
async def test_evaluation_api_lists_typed_runs_and_never_returns_private_fields() -> None:
    run_id = f"eval-zz-{uuid4().hex}"
    case_id = f"case-{uuid4().hex}"
    score: dict[str, Any] = {
        "scenario_id": "payment-failure-001",
        "mode": "multi",
        "seed": 7,
        "total": 0.85,
        "root_cause": {"value": 1, "fact_ids": ["diag-1"], "reason_codes": []},
        "root_cause_category": {
            "value": 1,
            "fact_ids": ["diag-1"],
            "reason_codes": [],
        },
        "evidence_fidelity": {
            "value": 1,
            "fact_ids": ["ev-1", "ev-2"],
            "reason_codes": [],
        },
        "signal_coverage": {
            "value": 1,
            "fact_ids": ["ev-1", "ev-2"],
            "reason_codes": [],
        },
        "tool_process": {"value": 1, "fact_ids": ["tc-1"], "reason_codes": []},
        "safety": {"value": 1, "fact_ids": [], "reason_codes": []},
        "recovery": {"value": 1, "fact_ids": ["case:recovery"], "reason_codes": []},
        "efficiency": {"value": 0, "fact_ids": ["mc-1"], "reason_codes": []},
        "hard_failures": [],
        "facts_digest": "a" * 64,
        "tool_call_count": 1,
        "model_tokens": 1000,
        "cost_microusd": 200,
        "duration_ms": 500,
        "trajectory_uri": "incidents/inc-1",
    }
    aggregate: dict[str, Any] = {
        "mode": "multi",
        "case_count": 1,
        "weighted_score": 0.85,
        "root_cause_accuracy": 1,
        "evidence_fidelity": 1,
        "safety_hard_failures": 0,
        "total_cost_microusd": 200,
        "total_duration_ms": 500,
        "total_tool_calls": 1,
    }
    database = Database(EVALUATION_URL)
    try:
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO evaluation_runs "
                    "(id, suite_version, candidate_version, status, aggregate_metrics) "
                    "VALUES (:id, 'validation-v1', 'active-v1', 'completed', "
                    "CAST(:metrics AS jsonb))"
                ),
                {"id": run_id, "metrics": json.dumps(aggregate)},
            )
            await connection.execute(
                text(
                    "INSERT INTO evaluation_cases "
                    "(id, run_id, scenario_id, metrics, hard_failures) "
                    "VALUES (:id, :run_id, 'payment-failure-001', CAST(:metrics AS jsonb), "
                    "CAST('[]' AS jsonb))"
                ),
                {"id": case_id, "run_id": run_id, "metrics": json.dumps(score)},
            )

        app = _app()
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                unauthorized = await client.get("/api/v1/evaluations/runs")
                listing = await client.get("/api/v1/evaluations/runs", headers=VIEWER)
                detail = await client.get(f"/api/v1/evaluations/runs/{run_id}", headers=VIEWER)
                missing = await client.get(
                    "/api/v1/evaluations/runs/missing-evaluation", headers=VIEWER
                )

        assert unauthorized.status_code == 401
        assert listing.status_code == 200
        assert any(item["id"] == run_id for item in listing.json())
        assert detail.status_code == 200
        assert detail.json()["cases"][0]["metrics"]["total"] == 0.85
        assert "scenario_key" not in json.dumps(detail.json())
        assert "ground_truth" not in json.dumps(detail.json())
        assert missing.status_code == 404
    finally:
        await database.dispose()
