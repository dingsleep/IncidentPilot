from __future__ import annotations

from collections import defaultdict

from incidentpilot.domain import DomainModel
from incidentpilot.evolution.trace_export import ExportedTrajectory, quality_reasons


class DatasetBuildResult(DomainModel):
    samples: list[ExportedTrajectory]
    rejections: dict[str, list[str]]


def build_dataset(trajectories: list[ExportedTrajectory]) -> DatasetBuildResult:
    accepted: list[ExportedTrajectory] = []
    rejections: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for trajectory in trajectories:
        reasons = quality_reasons(trajectory.quality)
        if trajectory.content_digest in seen:
            reasons.append("DUPLICATE_TRAJECTORY")
        if reasons:
            rejections[trajectory.provenance.scenario_id].extend(reasons)
            continue
        seen.add(trajectory.content_digest)
        accepted.append(trajectory)
    return DatasetBuildResult(samples=accepted, rejections=dict(rejections))
