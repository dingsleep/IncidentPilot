from __future__ import annotations

import pytest

from incidentpilot.remediation.adapters.docker import (
    DockerRestartAdapter,
    RestartTargetDeniedError,
)
from incidentpilot.remediation.executor import ActionExecutor


class FakeContainer:
    def __init__(self) -> None:
        self.timeouts: list[int] = []

    def restart(self, *, timeout: int) -> None:
        self.timeouts.append(timeout)


class FakeContainers:
    def __init__(self, container: FakeContainer) -> None:
        self._container = container
        self.requested_names: list[str] = []

    def get(self, name: str) -> FakeContainer:
        self.requested_names.append(name)
        return self._container


class FakeDockerClient:
    def __init__(self, container: FakeContainer) -> None:
        self.containers = FakeContainers(container)


def test_restart_uses_only_fixed_catalog_container_name() -> None:
    container = FakeContainer()
    client = FakeDockerClient(container)
    adapter = DockerRestartAdapter(
        client=client,
        catalog_containers={"checkout": "checkout"},
    )

    receipt = adapter.restart(target_service="checkout", grace_period_seconds=30)

    assert client.containers.requested_names == ["checkout"]
    assert container.timeouts == [30]
    assert receipt.target_service == "checkout"
    assert receipt.reference == "docker:restart:checkout"


def test_restart_rejects_target_outside_fixed_catalog_without_docker_call() -> None:
    container = FakeContainer()
    client = FakeDockerClient(container)
    adapter = DockerRestartAdapter(
        client=client,
        catalog_containers={"checkout": "checkout"},
    )

    with pytest.raises(RestartTargetDeniedError, match="not allowlisted"):
        adapter.restart(target_service="checkout; rm -rf /", grace_period_seconds=30)

    assert client.containers.requested_names == []
    assert container.timeouts == []


def test_executor_returns_only_sanitized_execution_fields() -> None:
    container = FakeContainer()
    executor = ActionExecutor(
        docker=DockerRestartAdapter(
            client=FakeDockerClient(container),
            catalog_containers={"checkout": "checkout"},
        ),
        flagd=None,
    )

    result = executor.restart_service(
        execution_id="exec_001",
        target_service="checkout",
        grace_period_seconds=30,
    )

    assert set(result.model_dump()) == {
        "execution_id",
        "status",
        "target_service",
        "started_at",
        "finished_at",
        "reference",
    }
    assert result.reference == "docker:restart:checkout"
