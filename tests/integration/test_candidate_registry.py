from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from incidentpilot.evolution.registry import PromptVersionRegistry
from incidentpilot.runtime.database import Database

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)


@pytest.mark.integration
async def test_prompt_registry_keeps_one_active_prompt_and_preserves_rollback() -> None:
    database = Database(MIGRATION_URL)
    agent_name = f"evolution-agent-{uuid4().hex}"
    registry = PromptVersionRegistry(database)
    try:
        await registry.activate_approved_prompt(
            agent_name=agent_name,
            version="v1",
            content_digest="a" * 64,
        )
        await registry.activate_approved_prompt(
            agent_name=agent_name,
            version="v2",
            content_digest="b" * 64,
        )
        await registry.rollback_prompt(agent_name=agent_name, version="v1")

        async with database.engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT version, status FROM prompt_versions "
                        "WHERE agent_name = :agent_name ORDER BY version"
                    ),
                    {"agent_name": agent_name},
                )
            ).all()
        assert rows == [("v1", "active"), ("v2", "retired")]
    finally:
        await database.dispose()
