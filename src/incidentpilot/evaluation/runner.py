from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from pydantic import Field

from incidentpilot.domain import DomainModel
from incidentpilot.evaluation.isolation import (
    FlagdRestorationError,
    FlagdScenarioController,
    episode_environment_lock,
)
from incidentpilot.evaluation.loader import (
    ExecutionSpec,
    LoadedEpisode,
    RuntimeEpisodeInput,
    TrafficSpec,
)
from incidentpilot.observability.attributes import operation_span
from incidentpilot.observability.metrics import OperationalMetrics

TELEMETRY_STABILIZATION_SECONDS = 20


class SuiteContaminatedError(RuntimeError):
    """Raised when the shared Demo cannot safely run another Episode."""


class EnvironmentMetadata(DomainModel):
    demo_tag: str
    demo_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    prompt_version: str
    model_profile: str
    tool_version: str


class HealthSnapshot(DomainModel):
    healthy: bool
    details: dict[str, Any]


class EpisodeRunResult(DomainModel):
    scenario_id: str
    split: str
    seed: int
    environment: EnvironmentMetadata
    environment_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline: HealthSnapshot
    recovery: HealthSnapshot
    alert_reference: str
    score: dict[str, Any]
    started_at: datetime
    finished_at: datetime


class EpisodeRunner:
    def __init__(
        self,
        *,
        controller: FlagdScenarioController,
        preflight: Callable[[], EnvironmentMetadata],
        capture_health: Callable[[], HealthSnapshot],
        send_alert: Callable[[RuntimeEpisodeInput], str],
        run_agent: Callable[[RuntimeEpisodeInput, int], Any],
        score: Callable[[Any, ExecutionSpec], dict[str, Any]],
        drive_traffic: Callable[[TrafficSpec], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        recovered: Callable[[HealthSnapshot, HealthSnapshot], bool] | None = None,
        tracer_provider: TracerProvider | None = None,
        operational_metrics: OperationalMetrics | None = None,
    ) -> None:
        self._controller = controller
        self._preflight = preflight
        self._capture_health = capture_health
        self._send_alert = send_alert
        self._run_agent = run_agent
        self._score = score
        self._drive_traffic = drive_traffic
        self._sleep = sleep
        self._recovered: Callable[[HealthSnapshot, HealthSnapshot], bool] = recovered or (
            lambda baseline, recovery: recovery.healthy and recovery.details == baseline.details
        )
        self._blocked = False
        self._tracer_provider = tracer_provider
        self._operational_metrics = operational_metrics

    def run(self, episode: LoadedEpisode, *, seed: int) -> EpisodeRunResult:
        started = time.perf_counter()
        with operation_span(
            "incidentpilot.evaluation.run",
            attributes={"incidentpilot.evaluation.split": episode.split},
            provider=self._tracer_provider,
        ):
            try:
                result = self._run(episode, seed=seed)
            except Exception:
                if self._operational_metrics is not None:
                    self._operational_metrics.record_agent(
                        "evaluation", int((time.perf_counter() - started) * 1000), success=False
                    )
                raise
        if self._operational_metrics is not None:
            self._operational_metrics.record_agent(
                "evaluation", int((time.perf_counter() - started) * 1000), success=True
            )
            self._operational_metrics.record_recovery(recovered=result.recovery.healthy)
        return result

    def _run(self, episode: LoadedEpisode, *, seed: int) -> EpisodeRunResult:
        with episode_environment_lock():
            if self._blocked:
                raise SuiteContaminatedError("suite is blocked after an unhealthy recovery")
            started_at = datetime.now(UTC)
            try:
                environment = self._preflight()
                snapshot = self._controller.snapshot()
                baseline = self._capture_health()
            except Exception as exc:
                self._blocked = True
                raise SuiteContaminatedError("episode preflight failed") from exc
            if not baseline.healthy:
                self._blocked = True
                raise SuiteContaminatedError("episode preflight health check failed")

            episode_error: BaseException | None = None
            alert_reference = ""
            score: dict[str, Any] = {}
            variants = [
                (injection.scenario_key, injection.variant)
                for injection in episode.execution.injections
            ]
            try:
                with self._controller.activate_many(variants, snapshot=snapshot):
                    warmup_seconds = max(
                        (injection.warmup_seconds for injection in episode.execution.injections),
                        default=0,
                    )
                    if warmup_seconds:
                        self._sleep(warmup_seconds)
                    if traffic := episode.execution.traffic:
                        if self._drive_traffic is None:
                            raise RuntimeError("episode traffic driver is not configured")
                        self._drive_traffic(traffic)
                        self._sleep(TELEMETRY_STABILIZATION_SECONDS)
                    alert_reference = self._send_alert(episode.public_input)
                    agent_output = self._run_agent(episode.public_input, seed)
                    score = self._score(agent_output, episode.execution)
            except BaseException as exc:
                episode_error = exc

            try:
                recovery = self._capture_health()
            except Exception as exc:
                self._blocked = True
                raise SuiteContaminatedError("recovery health check failed") from exc
            if isinstance(episode_error, FlagdRestorationError) or not self._recovered(
                baseline, recovery
            ):
                self._blocked = True
                raise SuiteContaminatedError(
                    "recovery check failed; suite is blocked"
                ) from episode_error
            if episode_error is not None:
                raise episode_error

            return EpisodeRunResult(
                scenario_id=episode.id,
                split=episode.split,
                seed=seed,
                environment=environment,
                environment_digest=_environment_digest(environment, snapshot.digest),
                baseline=baseline,
                recovery=recovery,
                alert_reference=alert_reference,
                score=score,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )


def _environment_digest(metadata: EnvironmentMetadata, config_digest: str) -> str:
    canonical = json.dumps(
        {"metadata": metadata.model_dump(mode="json"), "config_digest": config_digest},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
