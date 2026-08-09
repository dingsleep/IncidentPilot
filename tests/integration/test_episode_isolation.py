from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any

import httpx
import pytest

from incidentpilot.evaluation.cli import drive_otel_demo_traffic
from incidentpilot.evaluation.isolation import FlagdScenarioController
from incidentpilot.evaluation.loader import (
    ExecutionSpec,
    LoadedEpisode,
    RuntimeEpisodeInput,
    load_episode_suite,
)
from incidentpilot.evaluation.runner import (
    TELEMETRY_STABILIZATION_SECONDS,
    EnvironmentMetadata,
    EpisodeRunner,
    HealthSnapshot,
    SuiteContaminatedError,
)
from incidentpilot.telemetry.backends.jaeger import JaegerBackend
from incidentpilot.telemetry.schemas import TraceSearch

ROOT = Path(__file__).parents[2]
FLAGD_API = "http://127.0.0.1:4000/api"


def _episode() -> LoadedEpisode:
    episodes = load_episode_suite(ROOT / "scenarios", ROOT / "service_catalog" / "otel-demo.yaml")
    return next(episode for episode in episodes if episode.id == "payment-failure-001")


def _episode_by_id(episode_id: str) -> LoadedEpisode:
    episodes = load_episode_suite(ROOT / "scenarios", ROOT / "service_catalog" / "otel-demo.yaml")
    return next(episode for episode in episodes if episode.id == episode_id)


def _variant(controller: FlagdScenarioController) -> str:
    return str(controller.read_config()["flags"]["paymentFailure"]["defaultVariant"])


def _metadata() -> EnvironmentMetadata:
    return EnvironmentMetadata(
        demo_tag="2.2.0",
        demo_commit="b74a7bc7bbe66099c61951f42b24dab8b6f02d18",
        prompt_version="v1",
        model_profile="scripted",
        tool_version="telemetry-v3",
    )


def _health(controller: FlagdScenarioController, client: httpx.Client) -> HealthSnapshot:
    storefront = client.get("http://127.0.0.1:8080/")
    prometheus = client.get("http://127.0.0.1:9090/-/ready")
    return HealthSnapshot(
        healthy=storefront.is_success and prometheus.is_success,
        details={
            "paymentFailure": _variant(controller),
            "storefront_status": storefront.status_code,
            "prometheus_status": prometheus.status_code,
        },
    )


@pytest.mark.integration
def test_runner_orders_real_fault_episode_and_records_reproducibility() -> None:
    client = httpx.Client(timeout=5, trust_env=False)
    controller = FlagdScenarioController(client=client, base_url=FLAGD_API)
    original = controller.snapshot()
    events: list[str] = []

    def preflight() -> EnvironmentMetadata:
        events.append("preflight")
        return _metadata()

    def health() -> HealthSnapshot:
        events.append("health")
        return _health(controller, client)

    def alert(public: RuntimeEpisodeInput) -> str:
        events.append("alert")
        assert not hasattr(public, "execution")
        return "alert-eval-001"

    def drive_traffic(traffic: object) -> None:
        events.append("traffic")
        assert _variant(controller) == "100%"
        assert traffic == _episode().execution.traffic

    def agent(public: RuntimeEpisodeInput, seed: int) -> dict[str, Any]:
        events.append("agent")
        assert not hasattr(public, "execution")
        assert seed == 7
        assert _variant(controller) == "100%"
        return {"root_cause_service": "payment"}

    def score(output: object, execution: ExecutionSpec) -> dict[str, Any]:
        events.append("score")
        assert output == {"root_cause_service": "payment"}
        assert execution.injections[0].scenario_key == "paymentFailure"
        return {"root_cause": 1.0}

    runner = EpisodeRunner(
        controller=controller,
        preflight=preflight,
        capture_health=health,
        send_alert=alert,
        drive_traffic=drive_traffic,
        run_agent=agent,
        score=score,
        sleep=lambda seconds: events.append(f"warmup:{seconds}"),
    )
    try:
        result = runner.run(_episode(), seed=7)
        restored_digest = controller.snapshot().digest
    finally:
        client.close()

    assert events == [
        "preflight",
        "health",
        "warmup:30",
        "traffic",
        "warmup:20",
        "alert",
        "agent",
        "score",
        "health",
    ]
    assert result.scenario_id == "payment-failure-001"
    assert result.seed == 7
    assert result.environment.demo_tag == "2.2.0"
    assert result.environment_digest != original.digest
    assert result.alert_reference == "alert-eval-001"
    assert result.score == {"root_cause": 1.0}
    assert restored_digest == original.digest


@pytest.mark.integration
def test_real_episode_traffic_emits_payment_error_traces_and_restores_flag() -> None:
    client = httpx.Client(timeout=15, trust_env=False)
    controller = FlagdScenarioController(client=client, base_url=FLAGD_API)
    original = controller.snapshot()
    started_at = datetime.now(UTC) - timedelta(seconds=2)

    runner = EpisodeRunner(
        controller=controller,
        preflight=_metadata,
        capture_health=lambda: _health(controller, client),
        send_alert=lambda _public: "alert-traffic",
        drive_traffic=lambda traffic: drive_otel_demo_traffic(client, traffic),
        run_agent=lambda _public, _seed: {},
        score=lambda _output, _execution: {},
        sleep=time.sleep,
    )
    try:
        runner.run(_episode(), seed=8)
        ended_at = datetime.now(UTC)

        async def load_traces():
            async with httpx.AsyncClient(timeout=20, trust_env=False) as async_client:
                return await JaegerBackend(client=async_client).search(
                    TraceSearch(
                        services=["checkout", "payment"],
                        start=started_at,
                        end=ended_at,
                        limit=20,
                    )
                )

        traces = asyncio.run(load_traces())
        errors = [trace for trace in traces if trace.error]
        payment_errors = [
            trace for trace in errors if {"checkout", "payment"} <= set(trace.services)
        ]
        assert len(payment_errors) >= 6
        assert controller.snapshot().digest == original.digest
    finally:
        client.close()


@pytest.mark.integration
def test_real_cart_traffic_emits_cart_error_span_within_budget() -> None:
    episode = _episode_by_id("cart-failure-001")
    traffic = episode.execution.traffic
    assert traffic is not None
    client = httpx.Client(timeout=15, trust_env=False)
    controller = FlagdScenarioController(client=client, base_url=FLAGD_API)
    original = controller.snapshot()
    started_at = datetime.now(UTC) - timedelta(seconds=2)
    try:
        with controller.activate("cartFailure", "on"):
            drive_otel_demo_traffic(client, traffic.model_copy(update={"requests": 1}))
        time.sleep(8)
        ended_at = datetime.now(UTC)

        async def load_traces():
            async with httpx.AsyncClient(timeout=20, trust_env=False) as async_client:
                return await JaegerBackend(client=async_client).search(
                    TraceSearch(
                        services=["cart"],
                        start=started_at,
                        end=ended_at,
                        limit=20,
                    )
                )

        traces = asyncio.run(load_traces())
        assert any(
            span.service == "cart"
            and span.operation == "POST /oteldemo.CartService/EmptyCart"
            and span.status_code == "ERROR"
            for trace in traces
            for span in trace.error_spans
        )
        assert controller.snapshot().digest == original.digest
    finally:
        client.close()


@pytest.mark.integration
def test_real_ad_traffic_emits_ad_error_span_and_restores_flag() -> None:
    episode = _episode_by_id("ad-failure-001")
    traffic = episode.execution.traffic
    assert traffic is not None
    client = httpx.Client(timeout=20, trust_env=False)
    controller = FlagdScenarioController(client=client, base_url=FLAGD_API)
    original = controller.snapshot()
    started_at = datetime.now(UTC) - timedelta(seconds=2)
    try:
        with controller.activate("adFailure", "on"):
            drive_otel_demo_traffic(client, traffic)
        time.sleep(TELEMETRY_STABILIZATION_SECONDS)
        ended_at = datetime.now(UTC)

        async def load_traces():
            async with httpx.AsyncClient(timeout=20, trust_env=False) as async_client:
                return await JaegerBackend(client=async_client).search(
                    TraceSearch(
                        services=["frontend", "ad"],
                        start=started_at,
                        end=ended_at,
                        limit=20,
                    )
                )

        traces = asyncio.run(load_traces())
        assert any(
            span.service == "ad"
            and span.operation == "oteldemo.AdService/GetAds"
            and span.status_code == "ERROR"
            for trace in traces
            for span in trace.error_spans
        )
        metric = client.get(
            "http://127.0.0.1:9090/api/v1/query",
            params={
                "query": (
                    'sum(rate(traces_span_metrics_calls_total{service_name="ad",'
                    'status_code="STATUS_CODE_ERROR"}[2m]))'
                )
            },
        ).json()
        assert float(metric["data"]["result"][0]["value"][1]) > 0
        assert controller.snapshot().digest == original.digest
    finally:
        client.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("episode_id", "flag_key", "service", "operation"),
    [
        (
            "product-catalog-failure-001",
            "productCatalogFailure",
            "product-catalog",
            "oteldemo.ProductCatalogService/GetProduct",
        ),
        (
            "llm-rate-limit-001",
            "llmRateLimitError",
            "product-reviews",
            "get_ai_assistant_response",
        ),
    ],
)
def test_real_targeted_traffic_emits_error_span_and_restores_flag(
    episode_id: str,
    flag_key: str,
    service: str,
    operation: str,
) -> None:
    episode = _episode_by_id(episode_id)
    traffic = episode.execution.traffic
    assert traffic is not None
    client = httpx.Client(timeout=20, trust_env=False)
    controller = FlagdScenarioController(client=client, base_url=FLAGD_API)
    original = controller.snapshot()
    started_at = datetime.now(UTC) - timedelta(seconds=2)
    try:
        with controller.activate(flag_key, "on"):
            if flag_key == "llmRateLimitError":
                time.sleep(30)
            drive_otel_demo_traffic(client, traffic)
        time.sleep(8)
        ended_at = datetime.now(UTC)

        async def load_traces():
            async with httpx.AsyncClient(timeout=20, trust_env=False) as async_client:
                return await JaegerBackend(client=async_client).search(
                    TraceSearch(
                        services=[service], start=started_at, end=ended_at, limit=20
                    )
                )

        traces = asyncio.run(load_traces())
        assert any(
            span.service == service
            and span.operation == operation
            and span.status_code == "ERROR"
            for trace in traces
            for span in trace.error_spans
        )
        assert controller.snapshot().digest == original.digest
    finally:
        client.close()


@pytest.mark.integration
def test_real_recommendation_traffic_exposes_safe_cache_observations() -> None:
    episode = _episode_by_id("recommendation-cache-leak-001")
    client = httpx.Client(timeout=20, trust_env=False)
    controller = FlagdScenarioController(client=client, base_url=FLAGD_API)
    original = controller.snapshot()
    started_at = datetime.now(UTC) - timedelta(seconds=2)
    runner = EpisodeRunner(
        controller=controller,
        preflight=_metadata,
        capture_health=lambda: _health(controller, client),
        send_alert=lambda _public: "alert-recommendation",
        drive_traffic=lambda traffic: drive_otel_demo_traffic(client, traffic),
        run_agent=lambda _public, _seed: {},
        score=lambda _output, _execution: {},
        sleep=time.sleep,
    )
    try:
        runner.run(episode, seed=9)
        ended_at = datetime.now(UTC)

        async def load_traces():
            async with httpx.AsyncClient(timeout=20, trust_env=False) as async_client:
                return await JaegerBackend(client=async_client).search(
                    TraceSearch(
                        services=["recommendation"],
                        start=started_at,
                        end=ended_at,
                        limit=20,
                    )
                )

        traces = asyncio.run(load_traces())
        cache_observations = [
            observation
            for trace in traces
            for observation in trace.observations
            if observation.attributes.get("app.recommendation.cache_enabled") is True
        ]
        assert cache_observations
        assert any(
            observation.attributes.get("app.cache_hit") is False
            for observation in cache_observations
        )
        assert "app.user.id" not in " ".join(
            observation.model_dump_json() for observation in cache_observations
        )
        assert controller.snapshot().digest == original.digest
    finally:
        client.close()


@pytest.mark.integration
def test_runner_restores_real_fault_when_agent_fails() -> None:
    client = httpx.Client(timeout=5, trust_env=False)
    controller = FlagdScenarioController(client=client, base_url=FLAGD_API)
    original = controller.snapshot()
    health_calls = 0

    def health() -> HealthSnapshot:
        nonlocal health_calls
        health_calls += 1
        return _health(controller, client)

    def fail_agent(_public: RuntimeEpisodeInput, _seed: int) -> object:
        assert _variant(controller) == "100%"
        raise RuntimeError("scripted agent failure")

    runner = EpisodeRunner(
        controller=controller,
        preflight=_metadata,
        capture_health=health,
        send_alert=lambda _public: "alert-failure",
        drive_traffic=lambda _traffic: None,
        run_agent=fail_agent,
        score=lambda _output, _execution: {},
        sleep=lambda _seconds: None,
    )
    try:
        with pytest.raises(RuntimeError, match="scripted agent failure"):
            runner.run(_episode(), seed=11)
        assert controller.snapshot().digest == original.digest
        assert health_calls == 2
    finally:
        client.close()


@pytest.mark.integration
def test_global_environment_lock_serializes_fault_episodes() -> None:
    first_client = httpx.Client(timeout=5, trust_env=False)
    second_client = httpx.Client(timeout=5, trust_env=False)
    first_controller = FlagdScenarioController(client=first_client, base_url=FLAGD_API)
    second_controller = FlagdScenarioController(client=second_client, base_url=FLAGD_API)
    first_agent_entered = Event()
    release_first = Event()
    second_preflight_entered = Event()

    def first_agent(_public: RuntimeEpisodeInput, _seed: int) -> dict[str, Any]:
        first_agent_entered.set()
        assert release_first.wait(timeout=5)
        return {}

    def second_preflight() -> EnvironmentMetadata:
        second_preflight_entered.set()
        return _metadata()

    first_runner = EpisodeRunner(
        controller=first_controller,
        preflight=_metadata,
        capture_health=lambda: _health(first_controller, first_client),
        send_alert=lambda _public: "alert-first",
        drive_traffic=lambda _traffic: None,
        run_agent=first_agent,
        score=lambda _output, _execution: {},
        sleep=lambda _seconds: None,
    )
    second_runner = EpisodeRunner(
        controller=second_controller,
        preflight=second_preflight,
        capture_health=lambda: _health(second_controller, second_client),
        send_alert=lambda _public: "alert-second",
        drive_traffic=lambda _traffic: None,
        run_agent=lambda _public, _seed: {},
        score=lambda _output, _execution: {},
        sleep=lambda _seconds: None,
    )
    episode = _episode()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_runner.run, episode, seed=21)
            assert first_agent_entered.wait(timeout=5)
            second = pool.submit(second_runner.run, episode, seed=22)
            assert not second_preflight_entered.wait(timeout=0.2)
            release_first.set()
            first.result(timeout=10)
            second.result(timeout=10)
        assert second_preflight_entered.is_set()
    finally:
        release_first.set()
        first_client.close()
        second_client.close()


@pytest.mark.integration
def test_unhealthy_recovery_blocks_the_remaining_suite() -> None:
    client = httpx.Client(timeout=5, trust_env=False)
    controller = FlagdScenarioController(client=client, base_url=FLAGD_API)
    health_calls = 0
    preflight_calls = 0
    agent_entered = Event()
    release_agent = Event()

    def preflight() -> EnvironmentMetadata:
        nonlocal preflight_calls
        preflight_calls += 1
        return _metadata()

    def health() -> HealthSnapshot:
        nonlocal health_calls
        health_calls += 1
        snapshot = _health(controller, client)
        if health_calls == 1:
            return snapshot
        return HealthSnapshot(healthy=False, details=snapshot.details)

    def agent(_public: RuntimeEpisodeInput, _seed: int) -> dict[str, Any]:
        agent_entered.set()
        assert release_agent.wait(timeout=5)
        return {}

    runner = EpisodeRunner(
        controller=controller,
        preflight=preflight,
        capture_health=health,
        send_alert=lambda _public: "alert-contaminated",
        drive_traffic=lambda _traffic: None,
        run_agent=agent,
        score=lambda _output, _execution: {},
        sleep=lambda _seconds: None,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(runner.run, _episode(), seed=13)
            assert agent_entered.wait(timeout=5)
            second = pool.submit(runner.run, _episode(), seed=14)
            release_agent.set()
            with pytest.raises(SuiteContaminatedError, match="recovery check failed"):
                first.result(timeout=10)
            with pytest.raises(SuiteContaminatedError, match="suite is blocked"):
                second.result(timeout=10)
        assert preflight_calls == 1
        assert health_calls == 2
    finally:
        release_agent.set()
        client.close()
