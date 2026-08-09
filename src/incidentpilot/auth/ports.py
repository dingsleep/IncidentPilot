from __future__ import annotations

from datetime import timedelta
from typing import Protocol


class AuthTokenProvider(Protocol):
    def mint_telemetry_token(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        scopes: set[str],
        lifetime: timedelta = timedelta(minutes=10),
    ) -> str: ...
