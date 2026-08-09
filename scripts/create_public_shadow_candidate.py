from __future__ import annotations

import asyncio
from pathlib import Path

from incidentpilot.evolution.candidate_generator import generate_candidate
from incidentpilot.evolution.failure_mining import FailureCluster
from incidentpilot.evolution.registry import CandidateRegistry
from incidentpilot.orchestration.prompts import load_prompt_set
from incidentpilot.runtime.database import Database

ROOT = Path(__file__).resolve().parents[1]
EVALUATION_URL = (
    "postgresql+asyncpg://evaluation_role:evaluation-local-only@127.0.0.1:5433/incidentpilot"
)


async def main() -> None:
    prompt = load_prompt_set(ROOT / "prompts" / "v1").prompts["incident_commander"]
    candidate = generate_candidate(
        kind="prompt",
        cluster=FailureCluster(
            label="wrong_synthesis",
            affected_component="incident_commander",
            reason_codes=["ROOT_CAUSE_MISMATCH"],
            episode_ids=["eval-baseline-20260730023140-41"],
            representative_episode_id="eval-baseline-20260730023140-41",
        ),
        base_version=prompt.version,
        base_content=prompt.content,
        target_agent="incident_commander",
        generator_model="deterministic-m9.3",
    )
    database = Database(EVALUATION_URL)
    try:
        await CandidateRegistry(database).register_candidate(candidate)
    finally:
        await database.dispose()
    print(candidate.id)


if __name__ == "__main__":
    asyncio.run(main())
