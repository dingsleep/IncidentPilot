from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

_CONTAINER_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class DockerRestartError(RuntimeError):
    """Raised when the fixed Docker restart operation cannot complete."""


class RestartTargetDeniedError(DockerRestartError):
    """Raised before a target outside the server catalog reaches Docker."""


class DockerContainer(Protocol):
    def restart(self, *, timeout: int) -> object: ...


class DockerContainerCollection(Protocol):
    def get(self, name: str) -> DockerContainer: ...


class DockerClient(Protocol):
    @property
    def containers(self) -> DockerContainerCollection: ...


@dataclass(frozen=True)
class DockerRestartReceipt:
    target_service: str
    reference: str


class DockerRestartAdapter:
    """Restart a catalog-mapped local container through the Docker SDK only."""

    def __init__(
        self,
        *,
        client: DockerClient,
        catalog_containers: Mapping[str, str],
    ) -> None:
        if not catalog_containers:
            raise ValueError("catalog_containers must not be empty")
        if any(not _CONTAINER_NAME.fullmatch(name) for name in catalog_containers.values()):
            raise ValueError("catalog container names must be fixed Docker names")
        self._client = client
        self._catalog_containers = dict(catalog_containers)

    def restart(
        self,
        *,
        target_service: str,
        grace_period_seconds: int,
    ) -> DockerRestartReceipt:
        container_name = self._catalog_containers.get(target_service)
        if container_name is None:
            raise RestartTargetDeniedError("restart target is not allowlisted")
        try:
            self._client.containers.get(container_name).restart(timeout=grace_period_seconds)
        except Exception as exc:
            raise DockerRestartError("fixed Docker restart failed") from exc
        return DockerRestartReceipt(
            target_service=target_service,
            reference=f"docker:restart:{target_service}",
        )
