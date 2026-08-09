from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import os
import platform
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from psycopg_pool import ConnectionPool

from incidentpilot.domain.enums import Severity
from incidentpilot.incidents.service import IncidentService
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.job_queue import PostgresJobQueue, SingleJobQueue
from incidentpilot.worker.processor import JobProcessor

DEFAULT_DATABASE_URL = "postgresql://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
DEFAULT_API_DATABASE_URL = (
    "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot"
)
DEFAULT_WORKER_DATABASE_URL = (
    "postgresql+asyncpg://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot"
)
_MEMORY_VALUE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>KiB|MiB|GiB)$")


def percentile(samples: list[float], value: float) -> float:
    if not samples:
        raise ValueError("samples must not be empty")
    index = max(0, min(len(samples) - 1, round((len(samples) - 1) * value)))
    return sorted(samples)[index]


def measure_request(
    url: str,
    *,
    requests: int,
    concurrency: int,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    json: dict[str, object] | None = None,
) -> list[float]:
    with httpx.Client(timeout=10, trust_env=False) as client:
        warmup = client.request(method, url, headers=headers, json=json)
        warmup.raise_for_status()

        def one(_: int) -> float:
            started = time.perf_counter()
            response = client.request(method, url, headers=headers, json=json)
            response.raise_for_status()
            return (time.perf_counter() - started) * 1000

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            return list(pool.map(one, range(requests)))


def measure_database(
    database_url: str,
    *,
    requests: int,
    concurrency: int,
) -> tuple[list[float], dict[str, int]]:
    pool = ConnectionPool(conninfo=database_url, min_size=1, max_size=concurrency)
    try:
        def one(_: int) -> float:
            started = time.perf_counter()
            with pool.connection() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return (time.perf_counter() - started) * 1000

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            samples = list(executor.map(one, range(requests)))
        stats = pool.get_stats()
        return samples, {key: int(value) for key, value in stats.items()}
    finally:
        pool.close()


def measure_sse_first_event(url: str, *, headers: dict[str, str]) -> float:
    started = time.perf_counter()
    with (
        httpx.Client(timeout=10, trust_env=False) as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        response.raise_for_status()
        for chunk in response.iter_text():
            if chunk:
                return (time.perf_counter() - started) * 1000
    raise RuntimeError("SSE stream ended before its first event")


def docker_memory() -> tuple[str, dict[str, float]]:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker executable was not found")
    result = subprocess.run(  # noqa: S603 -- executable is resolved from the local PATH
        [docker, "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    output = result.stdout.strip()
    snapshots: dict[str, float] = {}
    for line in output.splitlines():
        name, _, usage = line.partition(" ")
        used, _, _ = usage.partition(" / ")
        if name and used:
            snapshots[name] = memory_mib(used)
    return output, snapshots


def environment_summary() -> str:
    docker = shutil.which("docker")
    docker_version = "unavailable"
    if docker is not None:
        result = subprocess.run(  # noqa: S603 -- executable is resolved from the local PATH
            [docker, "version", "--format", "{{.Server.Version}}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            docker_version = result.stdout.strip()
    return (
        f"{platform.platform()}; CPU logical cores={os.cpu_count()}; "
        f"Python={platform.python_version()}; Docker={docker_version}"
    )


def memory_mib(value: str) -> float:
    matched = _MEMORY_VALUE.fullmatch(value)
    if matched is None:
        raise ValueError(f"unsupported Docker memory value: {value}")
    multiplier = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}[matched["unit"]]
    return float(matched["value"]) * multiplier


def memory_growth_mib(before: dict[str, float], after: dict[str, float]) -> float:
    deltas = (after[name] - value for name, value in before.items() if name in after)
    return max(deltas, default=0.0)


def persistent_growth_mib(snapshots: list[dict[str, float]]) -> dict[str, float]:
    if len(snapshots) < 3:
        raise ValueError("persistent-growth analysis requires at least three snapshots")
    first, *rest = snapshots
    return {
        name: rest[-1][name] - initial
        for name, initial in first.items()
        if all(name in snapshot for snapshot in rest)
        if all(
            later[name] > earlier[name]
            for earlier, later in zip(snapshots, snapshots[1:], strict=False)
        )
    }


async def measure_job_wait(samples: int) -> list[float]:
    api_database = Database(DEFAULT_API_DATABASE_URL)
    worker_database = Database(DEFAULT_WORKER_DATABASE_URL)
    waits: list[float] = []
    try:
        for index in range(samples):
            enqueued_at = time.perf_counter()
            _, job_id = await IncidentService(api_database).create_manual(
                tenant_id="local",
                title=f"M8 queue benchmark {index + 1}",
                description="Local queue scheduling measurement; no diagnosis or action is run.",
                severity=Severity.P3,
                service="checkout",
                starts_at=datetime.now(UTC),
            )
            if job_id is None:
                raise RuntimeError("benchmark incident did not create an analysis job")
            claimed_at: float | None = None

            async def record_claim(_: object) -> None:
                nonlocal claimed_at
                claimed_at = time.perf_counter()

            handled = await JobProcessor(
                queue=SingleJobQueue(PostgresJobQueue(worker_database), job_id),
                worker_id="m8-queue-benchmark",
                handler=record_claim,
            ).run_once()
            if not handled or claimed_at is None:
                raise RuntimeError("benchmark job was not claimed")
            waits.append((claimed_at - enqueued_at) * 1000)
    finally:
        await worker_database.dispose()
        await api_database.dispose()
    return waits


def render(name: str, samples: list[float]) -> str:
    return (
        f"| {name} | {len(samples)} | {percentile(samples, .5):.1f} "
        f"| {percentile(samples, .95):.1f} |"
    )


def render_optional(name: str, samples: list[float] | None) -> str:
    return render(name, samples) if samples is not None else f"| {name} | — | — | — |"


def parse_header(value: str) -> tuple[str, str]:
    name, separator, header_value = value.partition(":")
    if not separator or not name.strip() or not header_value.strip():
        raise ValueError("headers must use Name: value format")
    return name.strip(), header_value.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure local IncidentPilot read paths.")
    parser.add_argument(
        "--api-url", default="http://127.0.0.1:8200/api/v1/incidents?limit=50"
    )
    parser.add_argument(
        "--api-header", action="append", default=["x-incidentpilot-actor: local-viewer"]
    )
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--sse-url")
    parser.add_argument("--mcp-url")
    parser.add_argument("--mcp-header", action="append", default=[])
    parser.add_argument("--requests", type=int, default=25)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--memory-interval-seconds", type=float, default=15)
    parser.add_argument("--memory-samples", type=int, default=3)
    parser.add_argument("--job-wait-samples", type=int, default=0)
    parser.add_argument("--graph-wall-ms", action="append", type=float, default=[])
    parser.add_argument(
        "--model-profile", default="HTTP/MCP: no model; Worker graph: scripted E2E agents"
    )
    parser.add_argument("--output", type=Path, default=Path("docs/reports/performance-baseline.md"))
    args = parser.parse_args()
    if (
        args.requests < 5
        or args.concurrency < 1
        or args.job_wait_samples < 0
        or args.memory_samples < 3
    ):
        raise ValueError("requests must be at least 5 and concurrency must be positive")
    if args.memory_interval_seconds < 0:
        raise ValueError("memory interval must be non-negative")
    api_headers = dict(parse_header(value) for value in args.api_header)
    mcp_headers = dict(parse_header(value) for value in args.mcp_header)
    api = measure_request(
        args.api_url, requests=args.requests, concurrency=args.concurrency, headers=api_headers
    )
    database, pool_stats = measure_database(
        args.database_url, requests=args.requests, concurrency=args.concurrency
    )
    sse = [measure_sse_first_event(args.sse_url, headers=api_headers)] if args.sse_url else None
    mcp = (
        measure_request(
            args.mcp_url,
            requests=args.requests,
            concurrency=args.concurrency,
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
                **mcp_headers,
            },
            method="POST",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "get_service_health_snapshot",
                    "arguments": {"services": ["checkout"], "window_minutes": 1},
                },
            },
        )
        if args.mcp_url
        else None
    )
    job_wait = (
        asyncio.run(measure_job_wait(args.job_wait_samples)) if args.job_wait_samples else None
    )
    graph_wall = args.graph_wall_ms or None
    memory_samples = [docker_memory()]
    for _ in range(args.memory_samples - 1):
        time.sleep(args.memory_interval_seconds)
        memory_samples.append(docker_memory())
    memory_growth = persistent_growth_mib([snapshot for _, snapshot in memory_samples])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "# Local performance baseline\n\n"
        f"- Sample time: {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
        f"- Concurrency: {args.concurrency}; samples per endpoint: {args.requests}\n"
        f"- Model profile: {args.model_profile}\n"
        f"- Hardware environment: {environment_summary()}\n"
        "- API is a five-concurrent-read incident-list request; database is a pooled "
        "`SELECT 1`.\n\n"
        "| Path | Samples | p50 ms | p95 ms |\n|---|---:|---:|---:|\n"
        f"{render('API incident list (non-LLM)', api)}\n"
        f"{render('PostgreSQL pooled read', database)}\n"
        f"{render_optional('SSE first event', sse)}\n"
        f"{render_optional('Telemetry MCP probe', mcp)}\n"
        f"{render_optional('Job enqueue to claim', job_wait)}\n"
        f"{render_optional('Worker graph E2E wall', graph_wall)}\n\n"
        "## Pool and memory\n\n"
        f"- PostgreSQL pool stats: `{pool_stats}`\n"
        f"- Memory samples: {args.memory_samples} at {args.memory_interval_seconds:g}s intervals\n"
        "- Persistent-growth verdict: "
        f"{'PASS' if not memory_growth else f'REVIEW {memory_growth}'}\n\n"
        "### First snapshot\n\n```text\n"
        f"{memory_samples[0][0]}\n```\n\n"
        "### Final snapshot\n\n```text\n"
        f"{memory_samples[-1][0]}\n```\n\n"
        "A dash means that endpoint was not supplied and is not a passing measurement. "
        "M8.4 requires an authenticated MCP probe, an existing incident SSE URL, and a real worker "
        "run before its complete performance gate can be checked.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
