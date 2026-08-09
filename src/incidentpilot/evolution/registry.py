from __future__ import annotations

from hashlib import sha256

from sqlalchemy import select, text

from incidentpilot.evolution.candidate_generator import CandidateArtifact
from incidentpilot.incidents.models import CandidateVersionRow, PromptVersionRow
from incidentpilot.runtime.database import Database


class CandidateRegistry:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def register_candidate(self, candidate: CandidateArtifact) -> None:
        async with self._database.session_factory() as session, session.begin():
            session.add(
                CandidateVersionRow(
                    id=candidate.id,
                    kind=candidate.kind,
                    base_version=candidate.base_version,
                    artifact_uri=f"evolution://candidates/{candidate.id}",
                    artifact_json=candidate.model_dump(mode="json"),
                    diff=candidate.diff,
                    target_failure_label=candidate.target_failure_label,
                    target_component=candidate.target_component,
                    generator_model=candidate.generator_model,
                    digest=candidate.digest,
                    status=candidate.status,
                )
            )

    async def load_candidate(self, candidate_id: str) -> CandidateArtifact:
        async with self._database.session_factory() as session:
            row = await session.get(CandidateVersionRow, candidate_id)
            if row is None:
                raise ValueError(f"unknown candidate: {candidate_id}")
            return CandidateArtifact.model_validate(row.artifact_json)


class PromptVersionRegistry:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def activate_approved_prompt(
        self,
        *,
        agent_name: str,
        version: str,
        content_digest: str,
    ) -> None:
        """Atomically retain the prior active row as the rollback version."""
        async with self._database.session_factory() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:agent_name, 0))"),
                {"agent_name": agent_name},
            )
            active = list(
                (
                    await session.execute(
                        select(PromptVersionRow)
                        .where(
                            PromptVersionRow.agent_name == agent_name,
                            PromptVersionRow.status == "active",
                        )
                        .with_for_update()
                    )
                ).scalars()
            )
            if len(active) > 1:
                raise RuntimeError(f"multiple active prompts found for {agent_name}")
            if (
                active
                and active[0].version == version
                and active[0].content_digest == content_digest
            ):
                return
            for row in active:
                row.status = "retired"
            session.add(
                PromptVersionRow(
                    id=f"prompt-{sha256(f'{agent_name}:{content_digest}'.encode()).hexdigest()[:24]}",
                    agent_name=agent_name,
                    version=version,
                    content_digest=content_digest,
                    status="active",
                )
            )

    async def rollback_prompt(self, *, agent_name: str, version: str) -> None:
        async with self._database.session_factory() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:agent_name, 0))"),
                {"agent_name": agent_name},
            )
            target = (
                await session.execute(
                    select(PromptVersionRow)
                    .where(
                        PromptVersionRow.agent_name == agent_name,
                        PromptVersionRow.version == version,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            await session.execute(
                text(
                    "UPDATE prompt_versions SET status = 'retired' "
                    "WHERE agent_name = :agent_name AND status = 'active'"
                ),
                {"agent_name": agent_name},
            )
            target.status = "active"
