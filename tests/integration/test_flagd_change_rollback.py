from __future__ import annotations

from copy import deepcopy
from typing import Any

import httpx
import pytest

from incidentpilot.evaluation.isolation import FlagdScenarioController
from incidentpilot.remediation.adapters.flagd import (
    FlagdChangeMapping,
    FlagdRollbackAdapter,
    FlagdRollbackConflictError,
    FlagdRollbackError,
    InMemoryFlagdChangeMappingStore,
)

FLAGD_API = "http://127.0.0.1:4000/api"


def _mapping(
    controller: FlagdScenarioController,
    config: dict[str, Any],
) -> FlagdChangeMapping:
    return FlagdChangeMapping(
        change_id="chg_payment_failure_001",
        target_service="checkout",
        flag_name="paymentFailure",
        restore_config=config,
        restore_digest=controller.digest(config),
    )


@pytest.mark.integration
def test_real_flagd_rollback_restores_private_change_snapshot() -> None:
    client = httpx.Client(timeout=5, trust_env=False)
    controller = FlagdScenarioController(client=client, base_url=FLAGD_API)
    original = controller.snapshot()
    adapter = FlagdRollbackAdapter(
        controller=controller,
        mappings=InMemoryFlagdChangeMappingStore([_mapping(controller, original.config)]),
    )
    try:
        with controller.activate("paymentFailure", "100%"):
            receipt = adapter.rollback(
                change_id="chg_payment_failure_001",
                target_service="checkout",
            )
            assert controller.snapshot().digest == original.digest
    finally:
        client.close()

    assert receipt.target_service == "checkout"
    assert receipt.reference == f"flagd:rollback:{original.digest}"


class FlagdStub:
    def __init__(self, config: dict[str, Any]) -> None:
        self.current = deepcopy(config)
        self.writes: list[dict[str, Any]] = []
        self.reads = 0
        self.mutate_on_read: int | None = None
        self.write_failures_remaining = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/feature/api/read":
            self.reads += 1
            if self.mutate_on_read == self.reads:
                self.current["flags"]["cartFailure"]["defaultVariant"] = "on"
            return httpx.Response(200, json=self.current)
        if request.method == "POST" and request.url.path == "/feature/api/write":
            written = httpx.Response(200, content=request.read()).json()["data"]
            self.writes.append(deepcopy(written))
            self.current = deepcopy(written)
            if self.write_failures_remaining:
                self.write_failures_remaining -= 1
                return httpx.Response(503, json={"error": "ambiguous write"})
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)


def _config() -> dict[str, Any]:
    return {
        "$schema": "https://flagd.dev/schema/v0/flags.json",
        "flags": {
            "paymentFailure": {
                "state": "ENABLED",
                "variants": {"off": 0, "100%": 1},
                "defaultVariant": "100%",
            },
            "cartFailure": {
                "state": "ENABLED",
                "variants": {"off": False, "on": True},
                "defaultVariant": "off",
            },
        },
    }


def _adapter(stub: FlagdStub) -> tuple[FlagdRollbackAdapter, dict[str, Any]]:
    client = httpx.Client(transport=httpx.MockTransport(stub.handle))
    controller = FlagdScenarioController(client=client, poll_interval=0, timeout=0.1)
    restore_config = _config()
    restore_config["flags"]["paymentFailure"]["defaultVariant"] = "off"
    return (
        FlagdRollbackAdapter(
            controller=controller,
            mappings=InMemoryFlagdChangeMappingStore([_mapping(controller, restore_config)]),
        ),
        restore_config,
    )


def test_rollback_detects_concurrent_config_change_before_write() -> None:
    stub = FlagdStub(_config())
    stub.mutate_on_read = 2
    adapter, _ = _adapter(stub)

    with pytest.raises(FlagdRollbackConflictError, match="changed before rollback"):
        adapter.rollback(change_id="chg_payment_failure_001", target_service="checkout")

    assert stub.writes == []
    assert stub.current["flags"]["cartFailure"]["defaultVariant"] == "on"


def test_rollback_compensates_ambiguous_partial_write_with_action_before_snapshot() -> None:
    initial = _config()
    stub = FlagdStub(initial)
    stub.write_failures_remaining = 1
    adapter, restore_config = _adapter(stub)

    with pytest.raises(FlagdRollbackError, match="write failed"):
        adapter.rollback(change_id="chg_payment_failure_001", target_service="checkout")

    assert stub.writes == [restore_config, initial]
    assert stub.current == initial
