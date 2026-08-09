from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from datetime import timedelta

from incidentpilot.auth.tokens import DevelopmentTelemetryTokenProvider


def mint_token(
    *,
    incident_id: str,
    tenant_id: str,
    scopes: set[str],
    audience: str,
    minutes: int,
    environment: Mapping[str, str],
) -> str:
    expected_audience = environment.get(
        "INCIDENTPILOT_TELEMETRY_AUDIENCE",
        "telemetry-mcp",
    )
    if audience != expected_audience or audience == "action-mcp":
        raise ValueError("mint_dev_token.py only signs Telemetry audience tokens")
    signing_key = environment.get("INCIDENTPILOT_TELEMETRY_SIGNING_KEY", "")
    if not signing_key or signing_key.startswith("replace-"):
        raise ValueError("INCIDENTPILOT_TELEMETRY_SIGNING_KEY must contain an Ed25519 key")
    return DevelopmentTelemetryTokenProvider(
        issuer=environment.get(
            "INCIDENTPILOT_TOKEN_ISSUER",
            "https://incidentpilot.local",
        ),
        audience=expected_audience,
        private_key=signing_key.replace("\\n", "\n"),
    ).mint_telemetry_token(
        tenant_id=tenant_id,
        incident_id=incident_id,
        scopes=scopes,
        lifetime=timedelta(minutes=minutes),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mint an incident-scoped development Telemetry JWT"
    )
    parser.add_argument("--incident-id", required=True)
    parser.add_argument("--tenant-id", default="local")
    parser.add_argument("--scope", action="append", required=True)
    parser.add_argument("--audience", default="telemetry-mcp")
    parser.add_argument("--minutes", type=int, default=10)
    args = parser.parse_args()
    try:
        token = mint_token(
            incident_id=args.incident_id,
            tenant_id=args.tenant_id,
            scopes=set(args.scope),
            audience=args.audience,
            minutes=args.minutes,
            environment=os.environ,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(token)


if __name__ == "__main__":
    main()
