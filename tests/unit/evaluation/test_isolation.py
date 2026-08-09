from __future__ import annotations

from copy import deepcopy
from typing import Any

import httpx
import pytest

from incidentpilot.evaluation.isolation import (
    FlagdRestorationError,
    FlagdScenarioController,
)


def _config() -> dict[str, Any]:
    return {
        "$schema": "https://flagd.dev/schema/v0/flags.json",
        "flags": {
            "paymentFailure": {
                "state": "ENABLED",
                "variants": {"off": 0, "100%": 1},
                "defaultVariant": "off",
            },
            "cartFailure": {
                "state": "ENABLED",
                "variants": {"off": False, "on": True},
                "defaultVariant": "off",
            },
        },
    }


class FlagdStub:
    def __init__(self, config: dict[str, Any], *, fail_restore: bool = False) -> None:
        self.original = deepcopy(config)
        self.current = deepcopy(config)
        self.fail_restore = fail_restore
        self.writes: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/feature/api/read":
            return httpx.Response(200, json=self.current)

        if request.method == "POST" and request.url.path == "/feature/api/write":
            data = request.read()
            body = httpx.Response(200, content=data).json()
            written = body["data"]
            self.writes.append(deepcopy(written))
            if self.fail_restore and written == self.original:
                return httpx.Response(500, json={"error": "restore rejected"})
            self.current = deepcopy(written)
            return httpx.Response(200, json={"status": "ok"})

        return httpx.Response(404)


def _controller(stub: FlagdStub) -> FlagdScenarioController:
    client = httpx.Client(transport=httpx.MockTransport(stub.handle))
    return FlagdScenarioController(client=client, poll_interval=0, timeout=0.1)


def test_activation_changes_only_target_flag_and_restores_full_snapshot() -> None:
    original = _config()
    stub = FlagdStub(original)
    controller = _controller(stub)

    with controller.activate("paymentFailure", "100%") as snapshot:
        expected_active = deepcopy(original)
        expected_active["flags"]["paymentFailure"]["defaultVariant"] = "100%"

        assert stub.current == expected_active
        assert snapshot.config == original
        assert snapshot.digest == controller.digest(original)

        stub.current["flags"]["cartFailure"]["defaultVariant"] = "on"
        assert snapshot.config == original

    assert stub.current == original
    assert stub.writes == [expected_active, original]


def test_activation_restores_snapshot_when_episode_assertion_fails() -> None:
    original = _config()
    stub = FlagdStub(original)
    controller = _controller(stub)

    with (
        pytest.raises(AssertionError, match="episode failed"),
        controller.activate("paymentFailure", "100%"),
    ):
        raise AssertionError("episode failed")

    assert stub.current == original


def test_restoration_failure_is_a_hard_error() -> None:
    stub = FlagdStub(_config(), fail_restore=True)
    controller = _controller(stub)

    with (
        pytest.raises(FlagdRestorationError, match="snapshot"),
        controller.activate("paymentFailure", "100%"),
    ):
        raise AssertionError("episode failed")


def test_multiple_injections_share_one_snapshot_and_restore_once() -> None:
    original = _config()
    stub = FlagdStub(original)
    controller = _controller(stub)

    with controller.activate_many([("paymentFailure", "100%"), ("cartFailure", "on")]) as snapshot:
        assert snapshot.config == original
        assert stub.current["flags"]["paymentFailure"]["defaultVariant"] == "100%"
        assert stub.current["flags"]["cartFailure"]["defaultVariant"] == "on"

    assert stub.current == original
    assert len(stub.writes) == 3
