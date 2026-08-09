from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from incidentpilot.domain import DomainModel
from incidentpilot.domain.alerts import TimeRange
from incidentpilot.domain.enums import EvidenceKind


class EvidenceRef(DomainModel):
    id: str
    incident_id: str
    kind: EvidenceKind
    source_system: str
    query: dict[str, Any]
    observed_range: TimeRange
    summary: str = Field(min_length=1, max_length=2000)
    source_uri: str | None = None
    raw_digest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    truncated: bool = False
    collected_at: datetime
