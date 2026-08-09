from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text

from incidentpilot.knowledge.models import RunbookHit
from incidentpilot.runtime.database import Database

_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)


class RunbookRetriever:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def search(
        self,
        *,
        query: str,
        services: Sequence[str],
        limit: int = 5,
        query_embedding: Sequence[float] | None = None,
    ) -> list[RunbookHit]:
        if not query.strip() or not services:
            raise ValueError("query and services are required")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be 1-20")
        candidates = max(limit * 3, 10)
        async with self._database.session_factory() as session:
            lexical = (
                (
                    await session.execute(
                        text(
                            """
                        WITH query AS (SELECT to_tsquery('english', :tsquery) AS value)
                        SELECT runbook_id, version, section_id, title, parent_title,
                               ts_headline(
                                   'english', content, query.value,
                                   'MaxFragments=2,MaxWords=35,MinWords=10'
                               ) AS snippet,
                               checksum
                        FROM runbook_sections, query
                        WHERE services ?| CAST(:services AS text[])
                          AND search_vector @@ query.value
                        ORDER BY ts_rank_cd(search_vector, query.value) DESC,
                                 runbook_id, section_id
                        LIMIT :limit
                        """
                        ),
                        {
                            "tsquery": _tsquery(query),
                            "services": list(services),
                            "limit": candidates,
                        },
                    )
                )
                .mappings()
                .all()
            )
            vector_rows: Sequence[Any] = []
            if query_embedding is not None:
                vector_rows = (
                    (
                        await session.execute(
                            text(
                                """
                            SELECT runbook_id, version, section_id, title, parent_title,
                                   left(content, 500) AS snippet, checksum
                            FROM runbook_sections
                            WHERE services ?| CAST(:services AS text[])
                              AND embedding IS NOT NULL
                            ORDER BY embedding <=> CAST(:embedding AS vector)
                            LIMIT :limit
                            """
                            ),
                            {
                                "services": list(services),
                                "embedding": _vector_literal(query_embedding),
                                "limit": candidates,
                            },
                        )
                    )
                    .mappings()
                    .all()
                )
        return _rrf(lexical, vector_rows, limit)

    async def get_by_checksum(self, checksum: str) -> RunbookHit | None:
        async with self._database.session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT runbook_id, version, section_id, title, parent_title,
                               content AS snippet, checksum
                        FROM runbook_sections
                        WHERE checksum = :checksum
                        """
                        ),
                        {"checksum": checksum},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return RunbookHit(**dict(row), score=1.0)


def _rrf(
    lexical: Sequence[Any],
    vector: Sequence[Any],
    limit: int,
) -> list[RunbookHit]:
    ranked: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
    for rows in (lexical, vector):
        for rank, row in enumerate(rows, start=1):
            data = dict(row)
            key = (data["runbook_id"], data["version"], data["section_id"])
            score, _ = ranked.get(key, (0.0, data))
            ranked[key] = (score + 1 / (60 + rank), data)
    ordered = sorted(ranked.values(), key=lambda item: item[0], reverse=True)[:limit]
    return [
        RunbookHit(
            runbook_id=data["runbook_id"],
            version=data["version"],
            section_id=data["section_id"],
            title=data["title"],
            parent_title=data["parent_title"],
            snippet=data["snippet"],
            checksum=data["checksum"],
            score=score,
        )
        for score, data in ordered
    ]


def _tsquery(value: str) -> str:
    tokens = list(dict.fromkeys(token.lower() for token in _TOKEN.findall(value)))[:30]
    if not tokens:
        raise ValueError("query must contain searchable terms")
    return " | ".join(tokens)


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in vector) + "]"
