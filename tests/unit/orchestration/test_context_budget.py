from __future__ import annotations

from datetime import UTC, datetime

from incidentpilot.domain.enums import EvidenceKind
from incidentpilot.orchestration.context import ContextBuilder, ContextEvidence


def _evidence(
    evidence_id: str,
    *,
    incident_id: str = "inc-1",
    services: tuple[str, ...] = ("checkout",),
    summary: str,
    source_uri: str,
) -> ContextEvidence:
    return ContextEvidence(
        evidence_id=evidence_id,
        incident_id=incident_id,
        kind=EvidenceKind.METRIC,
        services=services,
        summary=summary,
        source_uri=source_uri,
        collected_at=datetime(2026, 7, 17, tzinfo=UTC),
    )


def test_context_builder_filters_scope_and_preserves_numbers_and_sources() -> None:
    result = ContextBuilder(max_items=2, max_chars=280).build(
        incident_id="inc-1",
        scoped_services={"checkout"},
        evidence=[
            _evidence(
                "ev-1",
                summary="checkout p95=321.7ms and error_count=42",
                source_uri="prometheus://query/abc",
            ),
            _evidence(
                "ev-other-service",
                services=("email",),
                summary="email error_count=99",
                source_uri="prometheus://query/email",
            ),
            _evidence(
                "ev-other-incident",
                incident_id="inc-2",
                summary="checkout error_count=100",
                source_uri="prometheus://query/other",
            ),
            _evidence(
                "ev-2",
                summary="checkout request_rate=12.5/s",
                source_uri="jaeger://traces/001",
            ),
        ],
    )

    assert result.evidence_ids == ("ev-1", "ev-2")
    assert "321.7" in result.text
    assert "42" in result.text
    assert "prometheus://query/abc" in result.text
    assert "jaeger://traces/001" in result.text
    assert "ev-other" not in result.text
    assert result.char_count <= 280


def test_context_builder_skips_whole_item_when_character_budget_is_exhausted() -> None:
    result = ContextBuilder(max_items=5, max_chars=130).build(
        incident_id="inc-1",
        scoped_services={"checkout"},
        evidence=[
            _evidence(
                "ev-short",
                summary="error_count=7",
                source_uri="prometheus://query/short",
            ),
            _evidence(
                "ev-long",
                summary="latency=999ms " + "x" * 300,
                source_uri="prometheus://query/long",
            ),
        ],
    )

    assert result.evidence_ids == ("ev-short",)
    assert "999" not in result.text
