from __future__ import annotations

from collections.abc import Sequence, Set
from datetime import datetime
from urllib.parse import urlparse

from pydantic import Field

from incidentpilot.domain import DomainModel
from incidentpilot.domain.enums import EvidenceKind

_TRUSTED_CONTEXT_SOURCE_SCHEMES = frozenset(
    {"incidentpilot", "incidents", "jaeger", "opensearch", "prometheus", "runbooks"}
)


class ContextEvidence(DomainModel):
    evidence_id: str = Field(min_length=1, max_length=64)
    incident_id: str = Field(min_length=1, max_length=64)
    kind: EvidenceKind
    services: tuple[str, ...] = Field(min_length=1, max_length=20)
    summary: str = Field(min_length=1, max_length=2000)
    source_uri: str = Field(min_length=1, max_length=1000)
    collected_at: datetime


class ContextBundle(DomainModel):
    text: str
    evidence_ids: tuple[str, ...]
    char_count: int = Field(ge=0)


class ContextBuilder:
    def __init__(self, *, max_items: int = 40, max_chars: int = 32_000) -> None:
        if not 1 <= max_items <= 40:
            raise ValueError("max_items must be 1-40")
        if not 100 <= max_chars <= 100_000:
            raise ValueError("max_chars must be 100-100000")
        self._max_items = max_items
        self._max_chars = max_chars

    def build(
        self,
        *,
        incident_id: str,
        scoped_services: Set[str],
        evidence: Sequence[ContextEvidence],
    ) -> ContextBundle:
        lines: list[str] = []
        evidence_ids: list[str] = []
        char_count = 0
        for item in evidence:
            if item.incident_id != incident_id:
                continue
            if scoped_services.isdisjoint(item.services):
                continue
            line = (
                f"[{item.evidence_id}] kind={item.kind.value} "
                f"services={','.join(item.services)} source={_context_source_uri(item.source_uri)} "
                f"summary={item.summary[:800]}"
            )
            added = len(line) + (1 if lines else 0)
            if char_count + added > self._max_chars:
                continue
            lines.append(line)
            evidence_ids.append(item.evidence_id)
            char_count += added
            if len(lines) == self._max_items:
                break
        return ContextBundle(
            text="\n".join(lines),
            evidence_ids=tuple(evidence_ids),
            char_count=char_count,
        )


def _context_source_uri(source_uri: str) -> str:
    """Keep non-tool source links from becoming model-visible fetch instructions."""
    if urlparse(source_uri).scheme in _TRUSTED_CONTEXT_SOURCE_SCHEMES:
        return source_uri
    return "untrusted://external-reference"
