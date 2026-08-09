from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from incidentpilot.knowledge.indexer import RunbookIndexer
from incidentpilot.knowledge.loader import load_catalog
from incidentpilot.knowledge.retriever import RunbookRetriever
from incidentpilot.runtime.database import Database

ROOT = Path(__file__).parents[2]
MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)


class FakeEmbeddings:
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return [
            [1.0, 0.0, 0.0] if "recommendation" in text.lower() else [0.0, 1.0, 0.0]
            for text in texts
        ]


@pytest.mark.integration
async def test_postgres_runbook_retrieval_handles_synonyms_and_service_negatives() -> None:
    database = Database(MIGRATION_URL)
    try:
        runbooks = load_catalog(ROOT / "runbooks" / "catalog.yaml")
        await RunbookIndexer(database).index(runbooks)
        retriever = RunbookRetriever(database)

        dependency = await retriever.search(
            query="payment upstream cannot connect connection refused",
            services=["payment"],
            limit=5,
        )
        assert dependency
        assert dependency[0].runbook_id == "payment.dependency-unreachable"
        assert dependency[0].version == "1.0.0"
        assert dependency[0].section_id
        assert dependency[0].checksum

        memory = await retriever.search(
            query="recommendation out of memory heap keeps growing",
            services=["recommendation"],
            limit=5,
        )
        assert memory
        assert memory[0].runbook_id == "recommendation.memory-leak"
        assert all(hit.runbook_id != "email.memory-leak" for hit in memory[:1])

        await RunbookIndexer(database, embeddings=FakeEmbeddings()).index(runbooks)
        hybrid = await retriever.search(
            query="recommendation heap problem",
            services=["recommendation"],
            query_embedding=[1.0, 0.0, 0.0],
            limit=5,
        )
        assert hybrid
        assert hybrid[0].runbook_id == "recommendation.memory-leak"
    finally:
        await database.dispose()
