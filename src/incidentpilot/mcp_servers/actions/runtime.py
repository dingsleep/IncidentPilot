from __future__ import annotations

import argparse
import asyncio
import os

import httpx
import uvicorn

from incidentpilot.auth.tokens import (
    DevelopmentActionCatalogTokenVerifier,
    DevelopmentApprovalGrantTokenVerifier,
)
from incidentpilot.evaluation.isolation import FlagdScenarioController
from incidentpilot.mcp_servers.actions.server import create_action_mcp
from incidentpilot.mcp_servers.actions.tools import ActionToolHandlers, SqlAlchemyActionStore
from incidentpilot.mcp_servers.common.auth import RequestSizeLimitMiddleware
from incidentpilot.remediation.adapters.flagd import (
    FlagdRollbackAdapter,
    InMemoryFlagdChangeMappingStore,
)
from incidentpilot.remediation.executor import ActionExecutor
from incidentpilot.remediation.private_mappings import (
    PrivateMappingCipher,
    SqlAlchemyPrivateMappingRepository,
)
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.heartbeat import ProcessHeartbeat

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://action_mcp_role:action-local-only@127.0.0.1:5433/incidentpilot"
)


async def serve(args: argparse.Namespace) -> None:
    if os.environ.get("INCIDENTPILOT_ACTION_ENABLED", "").lower() not in {"1", "true"}:
        raise RuntimeError("INCIDENTPILOT_ACTION_ENABLED=true is required for Action MCP")
    issuer = os.environ.get("INCIDENTPILOT_TOKEN_ISSUER", "https://incidentpilot.local")
    audience = os.environ.get("INCIDENTPILOT_ACTION_AUDIENCE", "action-mcp")
    verifying_key = required_environment("INCIDENTPILOT_APPROVAL_VERIFYING_KEY").replace(
        "\\n", "\n"
    )
    cipher = PrivateMappingCipher.from_base64(
        required_environment("INCIDENTPILOT_PRIVATE_MAPPING_ENCRYPTION_KEY")
    )
    flagd_url = required_environment("INCIDENTPILOT_ACTION_FLAGD_API_URL")
    database = Database(
        os.environ.get("INCIDENTPILOT_ACTION_MCP_DATABASE_URL", DEFAULT_DATABASE_URL)
    )
    client = httpx.Client(timeout=10, trust_env=False)
    controller = FlagdScenarioController(client=client, base_url=flagd_url)
    executor = ActionExecutor(
        docker=None,
        flagd=FlagdRollbackAdapter(
            controller=controller,
            mappings=InMemoryFlagdChangeMappingStore(()),
        ),
    )
    store = SqlAlchemyActionStore(
        database=database,
        executor=executor,
        private_mappings=SqlAlchemyPrivateMappingRepository(database=database, cipher=cipher),
        allowed_actions=frozenset({"rollback_change"}),
    )
    mcp = create_action_mcp(
        handlers=ActionToolHandlers(store=store),
        token_verifier=DevelopmentApprovalGrantTokenVerifier(
            issuer=issuer,
            audience=audience,
            public_key=verifying_key,
        ),
        catalog_token_verifier=DevelopmentActionCatalogTokenVerifier(
            issuer=issuer,
            audience=audience,
            public_key=verifying_key,
        ),
        issuer=issuer,
        resource_server_url=f"http://{args.host}:{args.port}/mcp",
    )
    app = RequestSizeLimitMiddleware(
        mcp.streamable_http_app(),
        max_bytes=args.max_request_bytes,
    )
    stop = asyncio.Event()
    heartbeat = ProcessHeartbeat(database, process_name="action-mcp", instance_id="action-mcp")
    await heartbeat.ready()
    heartbeat_task = asyncio.create_task(heartbeat.maintain(stop))
    try:
        await uvicorn.Server(
            uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
        ).serve()
    finally:
        stop.set()
        await heartbeat_task
        await asyncio.to_thread(client.close)
        await database.dispose()


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value or value.startswith("replace-"):
        raise RuntimeError(f"{name} must be configured before Action MCP can start")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the approval-gated Action MCP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8102)
    parser.add_argument("--max-request-bytes", type=int, default=1_048_576)
    asyncio.run(serve(parser.parse_args()))


if __name__ == "__main__":
    main()
