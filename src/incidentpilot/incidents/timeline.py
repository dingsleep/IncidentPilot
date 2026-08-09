from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from incidentpilot.incidents.models import AuditEventRow
from incidentpilot.observability.redaction import redact_data


@dataclass(frozen=True)
class AuditEvent:
    id: str
    tenant_id: str
    incident_id: str | None
    actor_type: str
    actor_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
    prev_hash: str | None
    event_hash: str


def redact_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], redact_data(payload))


def _utc_timestamp(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("audit event time must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _event_hash(
    *,
    prev_hash: str | None,
    payload: dict[str, Any],
    event_type: str,
    actor_type: str,
    actor_id: str,
    created_at: datetime,
) -> str:
    canonical = json.dumps(
        {
            "actor": {"id": actor_id, "type": actor_type},
            "created_at": _utc_timestamp(created_at),
            "event_type": event_type,
            "payload": payload,
            "prev_hash": prev_hash,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def build_audit_event(
    *,
    event_id: str,
    tenant_id: str,
    incident_id: str | None,
    actor_type: str,
    actor_id: str,
    event_type: str,
    payload: dict[str, Any],
    created_at: datetime,
    prev_hash: str | None,
) -> AuditEvent:
    redacted = redact_audit_payload(payload)
    normalized_time = (
        created_at.astimezone(UTC) if created_at.utcoffset() is not None else created_at
    )
    event_hash = _event_hash(
        prev_hash=prev_hash,
        payload=redacted,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        created_at=normalized_time,
    )
    return AuditEvent(
        id=event_id,
        tenant_id=tenant_id,
        incident_id=incident_id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type=event_type,
        payload=redacted,
        created_at=normalized_time,
        prev_hash=prev_hash,
        event_hash=event_hash,
    )


def verify_audit_chain(events: list[AuditEvent]) -> bool:
    expected_prev: str | None = None
    for event in events:
        if event.prev_hash != expected_prev:
            return False
        expected_hash = _event_hash(
            prev_hash=event.prev_hash,
            payload=event.payload,
            event_type=event.event_type,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            created_at=event.created_at,
        )
        if not hmac.compare_digest(event.event_hash, expected_hash):
            return False
        expected_prev = event.event_hash
    return True


class AuditTimeline:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        event_id: str,
        tenant_id: str,
        incident_id: str | None,
        actor_type: str,
        actor_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> AuditEvent:
        chain_key = f"{tenant_id}:{incident_id or '-'}"
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:chain_key, 0))"),
            {"chain_key": chain_key},
        )
        previous = (
            await self._session.execute(self._latest_query(tenant_id, incident_id))
        ).scalar_one_or_none()
        created_at = cast(
            datetime,
            await self._session.scalar(select(func.clock_timestamp())),
        )
        if previous is not None and created_at <= previous.created_at:
            created_at = previous.created_at + timedelta(microseconds=1)
        event = build_audit_event(
            event_id=event_id,
            tenant_id=tenant_id,
            incident_id=incident_id,
            actor_type=actor_type,
            actor_id=actor_id,
            event_type=event_type,
            payload=payload,
            created_at=created_at,
            prev_hash=previous.event_hash if previous else None,
        )
        self._session.add(
            AuditEventRow(
                id=event.id,
                tenant_id=event.tenant_id,
                incident_id=event.incident_id,
                actor_type=event.actor_type,
                actor_id=event.actor_id,
                event_type=event.event_type,
                payload_json=event.payload,
                created_at=event.created_at,
                prev_hash=event.prev_hash,
                event_hash=event.event_hash,
            )
        )
        await self._session.flush()
        return event

    async def list_events(
        self,
        *,
        tenant_id: str,
        incident_id: str | None,
    ) -> list[AuditEvent]:
        query = self._chain_query(tenant_id, incident_id).order_by(
            AuditEventRow.created_at, AuditEventRow.id
        )
        rows = (await self._session.scalars(query)).all()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _chain_query(
        tenant_id: str,
        incident_id: str | None,
    ) -> Select[tuple[AuditEventRow]]:
        query = select(AuditEventRow).where(AuditEventRow.tenant_id == tenant_id)
        if incident_id is None:
            return query.where(AuditEventRow.incident_id.is_(None))
        return query.where(AuditEventRow.incident_id == incident_id)

    @classmethod
    def _latest_query(
        cls,
        tenant_id: str,
        incident_id: str | None,
    ) -> Select[tuple[AuditEventRow]]:
        return (
            cls._chain_query(tenant_id, incident_id)
            .order_by(AuditEventRow.created_at.desc(), AuditEventRow.id.desc())
            .limit(1)
        )

    @staticmethod
    def _from_row(row: AuditEventRow) -> AuditEvent:
        return AuditEvent(
            id=row.id,
            tenant_id=row.tenant_id,
            incident_id=row.incident_id,
            actor_type=row.actor_type,
            actor_id=row.actor_id,
            event_type=row.event_type,
            payload=row.payload_json,
            created_at=row.created_at,
            prev_hash=row.prev_hash,
            event_hash=row.event_hash,
        )
