from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import yaml
from mcp.server.fastmcp import FastMCP

from incidentpilot.auth.tokens import TELEMETRY_SCOPES
from incidentpilot.mcp_servers.common.auth import require_caller
from incidentpilot.mcp_servers.telemetry.tools import TelemetryToolHandlers


def load_service_catalog(path: Path) -> dict[str, Any]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("service catalog must contain an object")
    return cast(dict[str, Any], raw)


def register_telemetry_resources(
    mcp: FastMCP[Any],
    *,
    handlers: TelemetryToolHandlers,
    service_catalog: dict[str, Any] | None = None,
) -> None:
    if service_catalog is not None:

        @mcp.resource(
            "services://catalog",
            name="service_catalog",
            description="Read-only service ownership, criticality, and dependencies.",
            mime_type="application/json",
        )
        async def services_catalog() -> str:
            _require_any_telemetry_scope()
            return json.dumps(service_catalog, ensure_ascii=False, sort_keys=True)

        _ = services_catalog

    @mcp.resource(
        "incidents://{incident_id}/evidence/{evidence_id}",
        name="incident_evidence",
        description="Redacted Evidence owned by the token incident.",
        mime_type="application/json",
    )
    async def incident_evidence(incident_id: str, evidence_id: str) -> str:
        caller = _require_any_telemetry_scope()
        data = await handlers.get_evidence_resource(
            caller,
            incident_id=incident_id,
            evidence_id=evidence_id,
        )
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    _ = incident_evidence

    @mcp.resource(
        "runbooks://sections/{checksum}",
        name="runbook_section",
        description="One immutable versioned runbook section selected by checksum.",
        mime_type="application/json",
    )
    async def runbook_section(checksum: str) -> str:
        caller = _require_any_telemetry_scope()
        data = await handlers.get_runbook_resource(caller, checksum=checksum)
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    _ = runbook_section


def _require_any_telemetry_scope():
    for scope in sorted(TELEMETRY_SCOPES):
        try:
            return require_caller(scope)
        except PermissionError:
            continue
    raise PermissionError("a telemetry read scope is required")
