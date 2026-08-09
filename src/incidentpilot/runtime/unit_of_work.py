from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession

from incidentpilot.incidents.repository import SqlAlchemyIncidentRepository
from incidentpilot.incidents.timeline import AuditTimeline
from incidentpilot.runtime.database import Database
from incidentpilot.telemetry.evidence_store import (
    EvidenceStore,
    SqlAlchemyEvidenceRepository,
)


class UnitOfWork:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._session: AsyncSession | None = None
        self._incidents: SqlAlchemyIncidentRepository | None = None
        self._timeline: AuditTimeline | None = None
        self._evidence: EvidenceStore | None = None

    @property
    def incidents(self) -> SqlAlchemyIncidentRepository:
        if self._incidents is None:
            raise RuntimeError("UnitOfWork must be entered before use")
        return self._incidents

    @property
    def timeline(self) -> AuditTimeline:
        if self._timeline is None:
            raise RuntimeError("UnitOfWork must be entered before use")
        return self._timeline

    @property
    def evidence(self) -> EvidenceStore:
        if self._evidence is None:
            raise RuntimeError("UnitOfWork must be entered before use")
        return self._evidence

    async def __aenter__(self) -> UnitOfWork:
        self._session = self._database.session_factory()
        self._incidents = SqlAlchemyIncidentRepository(self._session)
        self._timeline = AuditTimeline(self._session)
        self._evidence = EvidenceStore(repository=SqlAlchemyEvidenceRepository(self._session))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is not None:
            await self._session.rollback()
            await self._session.close()
        self._session = None
        self._incidents = None
        self._timeline = None
        self._evidence = None

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("UnitOfWork must be entered before commit")
        await self._session.commit()

    async def rollback(self) -> None:
        if self._session is None:
            raise RuntimeError("UnitOfWork must be entered before rollback")
        await self._session.rollback()
