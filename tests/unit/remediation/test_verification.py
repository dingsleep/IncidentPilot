from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from incidentpilot.domain.actions import (
    ActionProposal,
    CompensationPlan,
    RestartServiceAction,
    VerificationCheck,
)
from incidentpilot.domain.enums import RiskLevel
from incidentpilot.remediation.verification import (
    PrometheusVerificationBaselineCollector,
    PrometheusVerificationReader,
    PrometheusVerificationSampler,
    ProposalVerificationService,
    VerificationObservation,
    verification_key,
)
from incidentpilot.telemetry.schemas import MetricPoint, MetricQuery, MetricSeries, MetricSeriesSet


def _proposal(*, threshold: float = 0.05) -> ActionProposal:
    check = VerificationCheck(
        service="payment",
        metric="error_ratio",
        query_template_id="service_error_ratio",
        comparator="lt",
        threshold=threshold,
        observation_seconds=30,
    )
    return ActionProposal(
        action=RestartServiceAction(target_service="payment", grace_period_seconds=30),
        risk=RiskLevel.LOW,
        diagnosis_evidence_ids=["ev-1", "ev-2"],
        expected_effect="Payment errors stop.",
        compensation_plan=CompensationPlan(
            mode="not_applicable", trigger="none", reason="Restart has no safe inverse."
        ),
        verification_checks=[check],
        verification_baseline={verification_key(check): 0.8},
        idempotency_key="restart-payment-1",
    )


@pytest.mark.asyncio
async def test_verification_waits_once_and_uses_the_persisted_baseline_and_comparator() -> None:
    observed_checks: list[VerificationCheck] = []
    waited: list[int] = []

    @dataclass
    class Reader:
        async def observe(
            self, *, incident_id: str, check: VerificationCheck
        ) -> VerificationObservation:
            assert incident_id == "inc-1"
            observed_checks.append(check)
            return VerificationObservation(value=0.01, evidence_id="ev-verified")

    async def wait(seconds: int) -> None:
        waited.append(seconds)

    proposal = _proposal()
    result = await ProposalVerificationService(reader=Reader(), wait=wait).verify(
        incident_id="inc-1", proposal=proposal
    )

    key = verification_key(proposal.verification_checks[0])
    assert waited == [30]
    assert observed_checks == proposal.verification_checks
    assert result.recovered is True
    assert result.degraded is False
    assert result.checks_passed == result.checks_total == 1
    assert result.baseline == {key: 0.8}
    assert result.observed == {key: 0.01}
    assert result.evidence_ids == ["ev-verified"]


@pytest.mark.asyncio
async def test_verification_never_reports_recovery_when_a_reading_misses_the_comparator() -> None:
    @dataclass
    class Reader:
        async def observe(
            self, *, incident_id: str, check: VerificationCheck
        ) -> VerificationObservation:
            del incident_id, check
            return VerificationObservation(value=0.8, evidence_id="ev-still-failing")

    async def no_wait(_: int) -> None:
        return None

    result = await ProposalVerificationService(reader=Reader(), wait=no_wait).verify(
        incident_id="inc-1", proposal=_proposal()
    )

    assert result.recovered is False
    assert result.degraded is True
    assert result.checks_passed == 0
    assert result.checks_total == 1


@pytest.mark.asyncio
async def test_prometheus_sampler_uses_only_the_check_template_and_latest_finite_value() -> None:
    captured: list[MetricQuery] = []
    now = datetime(2026, 8, 1, tzinfo=UTC)

    @dataclass
    class Metrics:
        async def query_range(self, request: MetricQuery) -> MetricSeriesSet:
            captured.append(request)
            return MetricSeriesSet(
                series=[
                    MetricSeries(
                        labels={},
                        points=[
                            MetricPoint(timestamp=now - timedelta(seconds=15), value=0.8),
                            MetricPoint(timestamp=now, value=0.01),
                        ],
                    )
                ],
                unit="ratio",
                raw_digest_sha256="a" * 64,
            )

    check = _proposal().verification_checks[0]
    value = await PrometheusVerificationSampler(metrics=Metrics(), clock=lambda: now).sample(
        check=check
    )

    assert value == 0.01
    assert captured[0].template_id == "service_error_ratio"
    assert captured[0].service == "payment"
    assert captured[0].end == now
    assert captured[0].start == now - timedelta(seconds=30)


@pytest.mark.asyncio
async def test_baseline_collector_records_every_proposal_check_by_its_stable_key() -> None:
    proposal = _proposal()

    @dataclass
    class Sampler:
        async def sample(self, *, check: VerificationCheck) -> float:
            assert check == proposal.verification_checks[0]
            return 0.8

    baseline = await PrometheusVerificationBaselineCollector(sampler=Sampler()).capture(
        proposal=proposal
    )

    assert baseline == {"payment:service_error_ratio:error_ratio": 0.8}


@pytest.mark.asyncio
async def test_prometheus_reader_records_each_observation_as_incident_evidence() -> None:
    proposal = _proposal()
    observed_at = datetime(2026, 8, 1, tzinfo=UTC)
    recorded: list[tuple[str, VerificationCheck, float, datetime]] = []

    @dataclass
    class Sampler:
        async def sample(self, *, check: VerificationCheck) -> float:
            assert check == proposal.verification_checks[0]
            return 0.01

    @dataclass
    class Evidence:
        async def record(
            self,
            *,
            incident_id: str,
            check: VerificationCheck,
            value: float,
            observed_at: datetime,
        ) -> str:
            recorded.append((incident_id, check, value, observed_at))
            return "ev-verification"

    observation = await PrometheusVerificationReader(
        sampler=Sampler(), evidence=Evidence(), clock=lambda: observed_at
    ).observe(incident_id="inc-1", check=proposal.verification_checks[0])

    assert observation == VerificationObservation(value=0.01, evidence_id="ev-verification")
    assert recorded == [("inc-1", proposal.verification_checks[0], 0.01, observed_at)]
