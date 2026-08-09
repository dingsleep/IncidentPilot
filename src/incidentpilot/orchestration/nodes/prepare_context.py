from __future__ import annotations

from typing import Any, Protocol

from incidentpilot.domain.alerts import TimeRange
from incidentpilot.domain.enums import EvidenceKind
from incidentpilot.domain.events import DomainInvariantError
from incidentpilot.domain.evidence import EvidenceRef
from incidentpilot.orchestration.state import (
    IncidentIdentity,
    PreparedContext,
    ServiceContext,
)


class ServerContextLoader(Protocol):
    async def get_incident_identity(self, incident_id: str) -> IncidentIdentity | None: ...

    async def load_service_catalog(self) -> list[ServiceContext]: ...

    async def load_recent_changes(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        services: list[str],
        time_range: TimeRange,
    ) -> list[EvidenceRef]: ...


class PrepareContextNode:
    """Load trusted context through a server-owned port, never model-provided credentials."""

    def __init__(self, loader: ServerContextLoader) -> None:
        self._loader = loader

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        incident_id = state["incident_id"]
        tenant_id = state["tenant_id"]
        scoped_services = state["scoped_services"]
        time_range = TimeRange.model_validate(state["time_range"])

        identity = await self._loader.get_incident_identity(incident_id)
        if identity is None or identity.incident_id != incident_id:
            raise DomainInvariantError("incident does not exist")
        if identity.tenant_id != tenant_id:
            raise DomainInvariantError("incident tenant does not match server identity")

        catalog = await self._loader.load_service_catalog()
        catalog_names = {service.name for service in catalog}
        unknown = set(scoped_services) - catalog_names
        if unknown:
            raise DomainInvariantError(f"service is not in catalog: {sorted(unknown)[0]}")

        changes = await self._loader.load_recent_changes(
            tenant_id=identity.tenant_id,
            incident_id=identity.incident_id,
            services=scoped_services,
            time_range=time_range,
        )
        for evidence in changes:
            if evidence.incident_id != incident_id or evidence.kind is not EvidenceKind.CHANGE:
                raise DomainInvariantError("recent changes must be persisted for current incident")
        change_ids = tuple(dict.fromkeys(item.id for item in changes))
        return {
            "prepared_context": PreparedContext(
                incident_id=incident_id,
                tenant_id=tenant_id,
                services=tuple(catalog),
                recent_change_evidence_ids=change_ids,
            ).model_dump(mode="json"),
            "evidence_ids": list(change_ids),
        }
