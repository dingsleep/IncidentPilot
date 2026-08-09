from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from incidentpilot.incidents.models import ServiceHeartbeatRow
from incidentpilot.runtime.database import Database


class ProcessHeartbeat:
    def __init__(self, database: Database, *, process_name: str, instance_id: str) -> None:
        self._database = database
        self._process_name = process_name
        self._instance_id = instance_id

    async def ready(self) -> None:
        now = datetime.now(UTC)
        async with self._database.session_factory() as session, session.begin():
            await session.execute(
                insert(ServiceHeartbeatRow)
                .values(
                    process_name=self._process_name,
                    instance_id=self._instance_id,
                    status="ready",
                    details_json={},
                    last_seen_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[
                        ServiceHeartbeatRow.process_name,
                        ServiceHeartbeatRow.instance_id,
                    ],
                    set_={"status": "ready", "details_json": {}, "last_seen_at": now},
                )
            )

    async def maintain(self, stop: asyncio.Event, *, interval_seconds: float = 30) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        while not stop.is_set():
            await self.ready()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
