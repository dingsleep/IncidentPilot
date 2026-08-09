from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from incidentpilot.knowledge.indexer import RunbookIndexer
from incidentpilot.knowledge.loader import load_catalog
from incidentpilot.runtime.database import Database

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)


async def _main(catalog: Path, database_url: str) -> None:
    database = Database(database_url)
    try:
        runbooks = load_catalog(catalog)
        await RunbookIndexer(database).index(runbooks)
        print(f"Indexed {len(runbooks)} runbooks")
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the versioned runbook index")
    parser.add_argument("--catalog", type=Path, default=Path("runbooks/catalog.yaml"))
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    args = parser.parse_args()
    asyncio.run(_main(args.catalog, args.database_url))


if __name__ == "__main__":
    main()
