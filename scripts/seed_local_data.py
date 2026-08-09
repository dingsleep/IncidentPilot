from __future__ import annotations

import asyncio
import os

from sqlalchemy.dialects.postgresql import insert

from incidentpilot.incidents.models import ActorRow, TenantRow
from incidentpilot.runtime.database import Database

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)


async def seed_local_data(database: Database) -> None:
    async with database.session_factory() as session, session.begin():
        await session.execute(
            insert(TenantRow)
            .values(id="local", name="Local Development")
            .on_conflict_do_nothing(index_elements=[TenantRow.id])
        )
        for role in ("viewer", "operator", "admin"):
            await session.execute(
                insert(ActorRow)
                .values(
                    id=f"local-{role}",
                    tenant_id="local",
                    display_name=f"Local {role.title()}",
                    role=role,
                )
                .on_conflict_do_nothing(index_elements=[ActorRow.id])
            )


async def _main() -> None:
    database = Database(
        os.environ.get("INCIDENTPILOT_MIGRATION_DATABASE_URL", DEFAULT_DATABASE_URL)
    )
    try:
        await seed_local_data(database)
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
