from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from incidentpilot.domain.actions import ActionProposal, VerificationCheck, VerificationResult
from incidentpilot.domain.alerts import TimeRange
from incidentpilot.domain.enums import EvidenceKind
from incidentpilot.runtime.database import Database
from incidentpilot.telemetry.evidence_store import (
    EvidenceStore,
    EvidenceWrite,
    SqlAlchemyEvidenceRepository,
)
from incidentpilot.telemetry.ports import MetricsBackend
from incidentpilot.telemetry.schemas import MetricQuery, MetricSeriesSet


@dataclass(frozen=True)
class VerificationObservation:
    value: float
    evidence_id: str


class VerificationMetricReader(Protocol):
    async def observe(
        self, *, incident_id: str, check: VerificationCheck
    ) -> VerificationObservation: ...


class PrometheusVerificationSampler:
    """Read only the metric template declared on a typed verification check."""

    def __init__(
        self,
        *,
        metrics: MetricsBackend,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._metrics = metrics
        self._clock = clock or (lambda: datetime.now(UTC))

    async def sample(self, *, check: VerificationCheck) -> float:
        end = self._clock()
        start = end - timedelta(seconds=check.observation_seconds)
        minutes = max(1, math.ceil(check.observation_seconds / 60))
        result = await self._metrics.query_range(
            MetricQuery(
                template_id=check.query_template_id,
                service=check.service,
                start=start,
                end=end,
                step_seconds=min(60, max(15, check.observation_seconds // 2)),
                duration=f"{minutes}m",
                window=f"{minutes}m",
            )
        )
        return _latest_metric_value(result)


class VerificationValueSampler(Protocol):
    async def sample(self, *, check: VerificationCheck) -> float: ...


class VerificationEvidenceRecorder(Protocol):
    async def record(
        self,
        *,
        incident_id: str,
        check: VerificationCheck,
        value: float,
        observed_at: datetime,
    ) -> str: ...


class PrometheusVerificationReader:
    """Turn a bounded Prometheus observation into a persisted Evidence reference."""

    def __init__(
        self,
        *,
        sampler: VerificationValueSampler,
        evidence: VerificationEvidenceRecorder,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sampler = sampler
        self._evidence = evidence
        self._clock = clock or (lambda: datetime.now(UTC))

    async def observe(
        self, *, incident_id: str, check: VerificationCheck
    ) -> VerificationObservation:
        value = await self._sampler.sample(check=check)
        observed_at = self._clock()
        evidence_id = await self._evidence.record(
            incident_id=incident_id,
            check=check,
            value=value,
            observed_at=observed_at,
        )
        return VerificationObservation(value=value, evidence_id=evidence_id)


class SqlAlchemyVerificationEvidenceRecorder:
    """Persist only the bounded metric value and template metadata as Evidence."""

    def __init__(self, *, database: Database) -> None:
        self._database = database

    async def record(
        self,
        *,
        incident_id: str,
        check: VerificationCheck,
        value: float,
        observed_at: datetime,
    ) -> str:
        if not math.isfinite(value):
            raise ValueError("verification evidence value must be finite")
        async with self._database.session_factory() as session, session.begin():
            reference = await EvidenceStore(
                repository=SqlAlchemyEvidenceRepository(session)
            ).persist(
                EvidenceWrite(
                    incident_id=incident_id,
                    kind=EvidenceKind.METRIC,
                    source_system="prometheus",
                    source_uri="prometheus://query_range",
                    query={
                        "template_id": check.query_template_id,
                        "service": check.service,
                        "metric": check.metric,
                        "observation_seconds": check.observation_seconds,
                    },
                    raw_json={"value": value},
                    observed_range=TimeRange(
                        start=observed_at - timedelta(seconds=check.observation_seconds),
                        end=observed_at,
                    ),
                    collected_at=observed_at,
                )
            )
        return reference.id


class PrometheusVerificationBaselineCollector:
    """Capture the proposal's action-before values through its fixed metric checks."""

    def __init__(self, *, sampler: VerificationValueSampler) -> None:
        self._sampler = sampler

    async def capture(self, *, proposal: ActionProposal) -> dict[str, float]:
        baseline: dict[str, float] = {}
        for check in proposal.verification_checks:
            key = verification_key(check)
            if key in baseline:
                raise ValueError("verification checks must be unique")
            value = await self._sampler.sample(check=check)
            if not math.isfinite(value):
                raise ValueError("verification baseline must be finite")
            baseline[key] = value
        return baseline


class ProposalVerificationService:
    """Compare post-action read-only observations with the saved proposal baseline."""

    def __init__(
        self,
        *,
        reader: VerificationMetricReader,
        wait: Callable[[int], Awaitable[None]],
    ) -> None:
        self._reader = reader
        self._wait = wait

    async def verify(self, *, incident_id: str, proposal: ActionProposal) -> VerificationResult:
        checks = proposal.verification_checks
        keys = [verification_key(check) for check in checks]
        if len(keys) != len(set(keys)):
            raise ValueError("verification checks must be unique")
        if set(proposal.verification_baseline) != set(keys):
            raise ValueError("proposal is missing a complete verification baseline")
        if not all(math.isfinite(value) for value in proposal.verification_baseline.values()):
            raise ValueError("proposal verification baseline must be finite")

        await self._wait(max(check.observation_seconds for check in checks))
        observed: dict[str, float] = {}
        evidence_ids: list[str] = []
        checks_passed = 0
        for check, key in zip(checks, keys, strict=True):
            observation = await self._reader.observe(incident_id=incident_id, check=check)
            if not math.isfinite(observation.value):
                raise ValueError("verification observation must be finite")
            observed[key] = observation.value
            evidence_ids.append(observation.evidence_id)
            checks_passed += _matches(check, observation.value)

        recovered = checks_passed == len(checks)
        return VerificationResult(
            recovered=recovered,
            degraded=not recovered,
            checks_passed=checks_passed,
            checks_total=len(checks),
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            baseline={key: proposal.verification_baseline[key] for key in keys},
            observed=observed,
            explanation=(
                "All saved verification comparators passed after the observation window."
                if recovered
                else (
                    "One or more saved verification comparators did not pass after the "
                    "observation window."
                )
            ),
        )


def verification_key(check: VerificationCheck) -> str:
    return f"{check.service}:{check.query_template_id}:{check.metric}"


def _matches(check: VerificationCheck, value: float) -> bool:
    threshold = check.threshold
    if check.comparator == "lt":
        return isinstance(threshold, float) and value < threshold
    if check.comparator == "lte":
        return isinstance(threshold, float) and value <= threshold
    if check.comparator == "gt":
        return isinstance(threshold, float) and value > threshold
    if check.comparator == "gte":
        return isinstance(threshold, float) and value >= threshold
    if not isinstance(threshold, list) or len(threshold) != 2:
        raise ValueError("between verification comparator requires two thresholds")
    lower, upper = threshold
    return lower <= value <= upper


def _latest_metric_value(result: MetricSeriesSet) -> float:
    if len(result.series) != 1 or not result.series[0].points:
        raise ValueError("verification metric must return exactly one non-empty aggregate series")
    value = result.series[0].points[-1].value
    if not math.isfinite(value):
        raise ValueError("verification metric value must be finite")
    return value
