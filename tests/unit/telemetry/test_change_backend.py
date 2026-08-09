from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from incidentpilot.evaluation.episode import run_flagd_episode
from incidentpilot.evaluation.isolation import FlagdScenarioController
from incidentpilot.telemetry.backends.changes import create_episode_change

ROOT = Path(__file__).parents[3]


def test_service_catalog_has_required_core_fields_and_valid_dependencies() -> None:
    catalog = yaml.safe_load((ROOT / "service_catalog" / "otel-demo.yaml").read_text())
    services = catalog["services"]
    by_name = {service["name"]: service for service in services}

    assert {
        "frontend",
        "checkout",
        "payment",
        "cart",
        "product-catalog",
        "recommendation",
        "shipping",
        "currency",
    } <= by_name.keys()
    for service in services:
        assert service["aliases"]
        assert service["criticality"] in {"critical", "high", "medium", "low"}
        assert service["protocol"] in {"http", "grpc", "kafka", "redis", "ofrep"}
        assert isinstance(service["allow_restart"], bool)
        assert service["owner"].endswith("-team")
        assert set(service["dependencies"]) <= by_name.keys()


def test_public_change_event_cannot_serialize_private_fault_mapping() -> None:
    occurred_at = datetime(2026, 7, 16, 8, 30, tzinfo=UTC)
    public, private = create_episode_change(
        service="checkout",
        scenario_key="paymentFailure",
        flag_name="paymentFailure",
        variant="100%",
        snapshot_digest="a" * 64,
        change_id="chg_test_001",
        occurred_at=occurred_at,
    )

    assert public.model_dump(mode="json") == {
        "change_id": "chg_test_001",
        "service": "checkout",
        "occurred_at": "2026-07-16T08:30:00Z",
        "change_type": "configuration",
        "summary": "Configuration change applied to checkout",
    }
    serialized = public.model_dump_json()
    for forbidden in ("scenario_key", "flag_name", "ground_truth", "paymentFailure", "100%"):
        assert forbidden not in serialized

    assert private.change_id == public.change_id
    assert private.scenario_key == "paymentFailure"
    assert private.flag_name == "paymentFailure"
    assert private.variant == "100%"
    assert private.snapshot_digest == "a" * 64


def test_flagd_episode_creates_linked_change_records_during_injection() -> None:
    current: dict[str, Any] = {
        "flags": {
            "paymentFailure": {
                "defaultVariant": "off",
                "variants": {"off": 0, "100%": 1},
            }
        }
    }

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal current
        if request.method == "GET":
            return httpx.Response(200, json=current)
        body = httpx.Response(200, content=request.read()).json()
        current = body["data"]
        return httpx.Response(200, json={"status": "ok"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        controller = FlagdScenarioController(client=client, poll_interval=0, timeout=0.1)
        result = run_flagd_episode(
            controller,
            service="checkout",
            scenario_key="paymentFailure",
            flag_name="paymentFailure",
            variant="100%",
            change_id="chg_test_002",
            occurred_at=datetime(2026, 7, 16, 9, 0, tzinfo=UTC),
            observe=lambda: current["flags"]["paymentFailure"]["defaultVariant"],
        )

    assert result.observation == "100%"
    assert result.change.change_id == "chg_test_002"
    assert result.change.service == "checkout"
    assert result.private_mapping.change_id == result.change.change_id
    assert result.private_mapping.scenario_key == "paymentFailure"
    assert current["flags"]["paymentFailure"]["defaultVariant"] == "off"
