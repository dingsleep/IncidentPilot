from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from incidentpilot.incidents.timeline import (
    build_audit_event,
    verify_audit_chain,
)

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def test_audit_chain_hashes_redacted_canonical_payload_and_detects_tampering() -> None:
    first = build_audit_event(
        event_id="audit-1",
        tenant_id="local",
        incident_id="inc-1",
        actor_type="user",
        actor_id="local-operator",
        event_type="incident.created",
        payload={"b": 2, "authorization": "Bearer secret", "a": 1},
        created_at=NOW,
        prev_hash=None,
    )
    second = build_audit_event(
        event_id="audit-2",
        tenant_id="local",
        incident_id="inc-1",
        actor_type="worker",
        actor_id="worker-1",
        event_type="incident.triaged",
        payload={"status": "TRIAGING"},
        created_at=NOW,
        prev_hash=first.event_hash,
    )
    equivalent = build_audit_event(
        event_id="audit-copy",
        tenant_id="local",
        incident_id="inc-1",
        actor_type="user",
        actor_id="local-operator",
        event_type="incident.created",
        payload={"a": 1, "authorization": "different secret", "b": 2},
        created_at=NOW,
        prev_hash=None,
    )

    assert first.payload == {"a": 1, "authorization": "[REDACTED]", "b": 2}
    assert equivalent.event_hash == first.event_hash
    assert verify_audit_chain([first, second])
    assert not verify_audit_chain([replace(first, payload={"a": 999}), second])


def test_audit_event_requires_timezone_aware_utc_input() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_audit_event(
            event_id="audit-1",
            tenant_id="local",
            incident_id=None,
            actor_type="system",
            actor_id="system",
            event_type="startup",
            payload={},
            created_at=datetime(2026, 7, 16, 12, 0),
            prev_hash=None,
        )
