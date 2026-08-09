from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from incidentpilot.api.main import create_app
from incidentpilot.config import ApiSettings, Settings
from incidentpilot.runtime.database import Database

API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
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
async def test_evolution_api_lists_candidate_diff_and_explainable_gate_record() -> None:
    candidate_id = f"candidate-{uuid4().hex[:12]}"
    database = Database(MIGRATION_URL)
    try:
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO candidate_versions "
                    "(id, kind, base_version, artifact_uri, artifact_json, diff, "
                    "target_failure_label, target_component, generator_model, digest, status) "
                    "VALUES (:id, 'prompt', 'v1', :uri, CAST(:artifact AS jsonb), :diff, "
                    "'wrong_synthesis', 'incident_commander', 'test-model', :digest, 'candidate')"
                ),
                {
                    "id": candidate_id,
                    "uri": f"evolution://candidates/{candidate_id}",
                    "artifact": json.dumps({"target_agent": "incident_commander"}),
                    "diff": "--- active\n+++ candidate",
                    "digest": uuid4().hex + uuid4().hex,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO promotion_gate_records "
                    "(id, candidate_id, status, decision_json) "
                    "VALUES (:id, :candidate_id, 'shadow_rejected', CAST(:decision AS jsonb))"
                ),
                {
                    "id": f"gate-{uuid4().hex}",
                    "candidate_id": candidate_id,
                    "decision": json.dumps({"reason": "validation regression"}),
                },
            )

        app = _app()
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                unauthorized = await client.get("/api/v1/evolution/candidates")
                response = await client.get("/api/v1/evolution/candidates", headers=VIEWER)

        assert unauthorized.status_code == 401
        assert response.status_code == 200
        payload = next(item for item in response.json() if item["id"] == candidate_id)
        assert payload["diff"] == "--- active\n+++ candidate"
        assert payload["gate_records"] == [
            {
                "status": "shadow_rejected",
                "decision": {"reason": "validation regression"},
                "human_rejection_reason": None,
            }
        ]
        assert "artifact_json" not in json.dumps(payload)
    finally:
        await database.dispose()
