from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any

from sqlalchemy import and_, or_, select

from incidentpilot.incidents.models import AuditEventRow
from incidentpilot.observability.redaction import redact_data
from incidentpilot.runtime.database import Database

SSE_BUFFER_LIMIT = 100
SSE_HEARTBEAT_SECONDS = 15.0
SSE_POLL_SECONDS = 0.5
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_EVENT_TYPES = frozenset(
    {
        "run.started",
        "incident.status_changed",
        "agent.started",
        "agent.completed",
        "tool.started",
        "tool.completed",
        "stage.completed",
        "evidence.created",
        "hypothesis.updated",
        "diagnosis.created",
        "approval.requested",
        "action.completed",
        "verification.completed",
        "incident.completed",
        "run.failed",
    }
)


@dataclass(frozen=True)
class SseEvent:
    id: str
    event: str
    data: dict[str, Any]

    def encode(self) -> str:
        payload = json.dumps(
            self.data,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return f"id: {self.id}\nevent: {self.event}\ndata: {payload}\n\n"


class SseRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def fetch(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        after: str | None,
        limit: int = SSE_BUFFER_LIMIT,
    ) -> list[SseEvent]:
        if limit < 1 or limit > SSE_BUFFER_LIMIT:
            raise ValueError(f"SSE buffer limit must be between 1 and {SSE_BUFFER_LIMIT}")
        cursor = parse_event_id(after) if after else None
        query = select(AuditEventRow).where(
            AuditEventRow.tenant_id == tenant_id,
            AuditEventRow.incident_id == incident_id,
        )
        if cursor is not None:
            created_at, audit_id = cursor
            query = query.where(
                or_(
                    AuditEventRow.created_at > created_at,
                    and_(
                        AuditEventRow.created_at == created_at,
                        AuditEventRow.id > audit_id,
                    ),
                )
            )
        async with self._database.session_factory() as session:
            rows = (
                await session.scalars(
                    query.order_by(AuditEventRow.created_at, AuditEventRow.id).limit(limit)
                )
            ).all()
        return [_to_sse_event(row) for row in rows]


async def stream_events(
    repository: SseRepository,
    *,
    tenant_id: str,
    incident_id: str,
    last_event_id: str | None = None,
    poll_interval_seconds: float = SSE_POLL_SECONDS,
    heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncGenerator[str]:
    if poll_interval_seconds <= 0 or heartbeat_seconds <= 0:
        raise ValueError("SSE polling and heartbeat intervals must be positive")
    if last_event_id is not None:
        parse_event_id(last_event_id)
    after = last_event_id
    last_output = monotonic()
    yield ": connected\n\n"
    while True:
        if is_disconnected is not None and await is_disconnected():
            return
        batch = await repository.fetch(
            tenant_id=tenant_id,
            incident_id=incident_id,
            after=after,
            limit=SSE_BUFFER_LIMIT,
        )
        if batch:
            for event in batch:
                after = event.id
                last_output = monotonic()
                yield event.encode()
            continue

        remaining = heartbeat_seconds - (monotonic() - last_output)
        if remaining <= 0:
            last_output = monotonic()
            yield ": heartbeat\n\n"
            continue
        await asyncio.sleep(min(poll_interval_seconds, remaining))


def parse_event_id(value: str) -> tuple[datetime, str]:
    micros_text, separator, audit_id = value.partition("-")
    if (
        not separator
        or len(micros_text) != 20
        or not micros_text.isascii()
        or not micros_text.isdigit()
        or not audit_id
        or len(audit_id) > 64
    ):
        raise ValueError("invalid SSE event ID")
    try:
        created_at = _EPOCH + timedelta(microseconds=int(micros_text))
    except OverflowError as exc:
        raise ValueError("invalid SSE event ID") from exc
    return created_at, audit_id


def _event_id(created_at: datetime, audit_id: str) -> str:
    normalized = created_at.astimezone(UTC)
    elapsed = normalized - _EPOCH
    micros = elapsed.days * 86_400_000_000 + elapsed.seconds * 1_000_000 + elapsed.microseconds
    return f"{micros:020d}-{audit_id}"


def _to_sse_event(row: AuditEventRow) -> SseEvent:
    return SseEvent(
        id=_event_id(row.created_at, row.id),
        event=_public_event_type(row.event_type),
        data={
            "schema_version": 1,
            "audit_event_id": row.id,
            "created_at": row.created_at.astimezone(UTC).isoformat(),
            "actor": {"id": row.actor_id, "type": row.actor_type},
            "payload": redact_data(row.payload_json),
        },
    )


def _public_event_type(event_type: str) -> str:
    if event_type in _EVENT_TYPES:
        return event_type
    if event_type == "GRAPH_COMPLETED":
        return "incident.completed"
    if "fail" in event_type.casefold() or "error" in event_type.casefold():
        return "run.failed"
    return "incident.status_changed"
