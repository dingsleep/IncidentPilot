from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from incidentpilot.evaluation.isolation import FlagdScenarioController
from incidentpilot.telemetry.backends.changes import (
    ChangeEvent,
    PrivateChangeMapping,
    create_episode_change,
)


@dataclass(frozen=True)
class FlagdEpisodeResult[ResultT]:
    observation: ResultT
    change: ChangeEvent
    private_mapping: PrivateChangeMapping


def run_flagd_episode[ResultT](
    controller: FlagdScenarioController,
    *,
    service: str,
    scenario_key: str,
    flag_name: str,
    variant: str,
    change_id: str | None = None,
    occurred_at: datetime | None = None,
    observe: Callable[[], ResultT],
) -> FlagdEpisodeResult[ResultT]:
    """Run one observation while a single flag variant is active."""
    with controller.activate(flag_name, variant) as snapshot:
        change, private_mapping = create_episode_change(
            service=service,
            scenario_key=scenario_key,
            flag_name=flag_name,
            variant=variant,
            snapshot_digest=snapshot.digest,
            change_id=change_id,
            occurred_at=occurred_at,
        )
        observation = observe()
    return FlagdEpisodeResult(
        observation=observation,
        change=change,
        private_mapping=private_mapping,
    )
