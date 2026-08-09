from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from incidentpilot.api.main import create_app
from incidentpilot.config import ActionSettings, ApiSettings, Settings
from incidentpilot.runtime.database import Database

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
API_URL = "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"


async def _upsert_ready_heartbeat(database: Database, process_name: str) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO service_heartbeats (
                    process_name, instance_id, status, details_json, last_seen_at
                ) VALUES (:process_name, 'm5-health-test', 'ready', '{}'::jsonb, :now)
                ON CONFLICT (process_name, instance_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    details_json = EXCLUDED.details_json,
                    last_seen_at = EXCLUDED.last_seen_at
                """
            ),
            {"process_name": process_name, "now": datetime.now(UTC)},
        )


@pytest.mark.integration
async def test_health_reports_database_queue_and_process_heartbeats() -> None:
    migration_database = Database(MIGRATION_URL)
    try:
        await _upsert_ready_heartbeat(migration_database, "worker")
        await _upsert_ready_heartbeat(migration_database, "telemetry-mcp")
    finally:
        await migration_database.dispose()

    app = create_app(
        Settings(
            environment="development",
            api=ApiSettings(database_url=SecretStr(API_URL)),
            actions=ActionSettings(enabled=False),
        )
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            live = await client.get("/api/v1/health/live")
            ready = await client.get("/api/v1/health/ready")

        assert live.status_code == 200
        assert live.json() == {"status": "live"}
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert ready.json()["checks"] == {
            "api_db": {"status": "ready"},
            "job_queue": {"status": "ready"},
            "worker": {"status": "ready"},
            "telemetry_mcp": {"status": "ready"},
            "action_mcp": {"status": "disabled"},
        }
        runtime = app.state.runtime
        assert runtime.database.engine.url.username == "incident_api_role"
        assert not hasattr(runtime, "llm")
        assert not hasattr(runtime, "mcp")
        assert not hasattr(runtime, "graph")
        assert not hasattr(runtime, "checkpointer")


@pytest.mark.integration
async def test_problem_details_are_correlated_and_sanitized() -> None:
    app = create_app(
        Settings(
            environment="development",
            api=ApiSettings(database_url=SecretStr(API_URL)),
            actions=ActionSettings(enabled=False),
        )
    )

    async def fail_safely() -> None:
        raise RuntimeError("token=secret SELECT * FROM actors at http://internal.example/database")

    app.add_api_route("/api/v1/_test/error", fail_safely, methods=["GET"])

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/_test/error",
                headers={"X-Correlation-ID": "health-test-correlation"},
            )

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["x-correlation-id"] == "health-test-correlation"
    assert response.json() == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "detail": "The server could not complete the request.",
        "code": "INTERNAL_ERROR",
        "correlation_id": "health-test-correlation",
    }
    body = response.text.lower()
    for secret in ("secret", "select", "actors", "internal.example", "database"):
        assert secret not in body
