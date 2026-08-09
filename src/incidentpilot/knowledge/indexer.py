from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import text

from incidentpilot.knowledge.models import RunbookDocument
from incidentpilot.runtime.database import Database


class EmbeddingProvider(Protocol):
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class RunbookIndexer:
    def __init__(
        self,
        database: Database,
        *,
        embeddings: EmbeddingProvider | None = None,
    ) -> None:
        self._database = database
        self._embeddings = embeddings

    async def index(self, runbooks: Sequence[RunbookDocument]) -> None:
        sections = [section for runbook in runbooks for section in runbook.sections]
        vectors: Sequence[Sequence[float] | None]
        if self._embeddings is None:
            vectors = [None] * len(sections)
        else:
            embedded = await self._embeddings.embed(
                [f"{section.title}\n{section.content}" for section in sections]
            )
            if len(embedded) != len(sections):
                raise ValueError("embedding provider returned the wrong vector count")
            vectors = list(embedded)

        async with self._database.session_factory() as session, session.begin():
            for runbook in runbooks:
                await session.execute(
                    text(
                        """
                        INSERT INTO runbook_versions (id, version, content, digest, metadata_json)
                        VALUES (:id, :version, :content, :digest, CAST(:metadata AS jsonb))
                        ON CONFLICT (id, version) DO UPDATE SET
                            content = EXCLUDED.content,
                            digest = EXCLUDED.digest,
                            metadata_json = EXCLUDED.metadata_json
                        """
                    ),
                    {
                        "id": runbook.id,
                        "version": runbook.version,
                        "content": runbook.content,
                        "digest": runbook.digest,
                        "metadata": json.dumps(
                            runbook.model_dump(
                                mode="json",
                                exclude={"content", "digest", "sections"},
                            ),
                            sort_keys=True,
                        ),
                    },
                )
            for section, vector in zip(sections, vectors, strict=True):
                await session.execute(
                    text(
                        """
                        INSERT INTO runbook_sections (
                            runbook_id, version, section_id, title, parent_title,
                            content, checksum, services, symptoms, embedding
                        )
                        VALUES (
                            :runbook_id, :version, :section_id, :title, :parent_title,
                            :content, :checksum, CAST(:services AS jsonb),
                            CAST(:symptoms AS jsonb), CAST(:embedding AS vector)
                        )
                        ON CONFLICT (runbook_id, version, section_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            parent_title = EXCLUDED.parent_title,
                            content = EXCLUDED.content,
                            checksum = EXCLUDED.checksum,
                            services = EXCLUDED.services,
                            symptoms = EXCLUDED.symptoms,
                            embedding = EXCLUDED.embedding
                        """
                    ),
                    {
                        **section.model_dump(exclude={"runbook_digest", "services", "symptoms"}),
                        "services": json.dumps(section.services),
                        "symptoms": json.dumps(section.symptoms),
                        "embedding": _vector_literal(vector),
                    },
                )


def _vector_literal(vector: Sequence[float] | None) -> str | None:
    if vector is None:
        return None
    return "[" + ",".join(str(float(value)) for value in vector) + "]"
