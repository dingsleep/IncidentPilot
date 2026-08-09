from __future__ import annotations

import argparse
import asyncio
import os
import socket
from typing import Literal, cast

from incidentpilot.bootstrap import DatabaseInitialStateLoader
from incidentpilot.config import LlmSettings, ModelSettings
from incidentpilot.domain.alerts import TimeRange
from incidentpilot.evaluation.cli import (
    DEFAULT_TELEMETRY_DATABASE_URL,
    DEFAULT_WORKER_DATABASE_URL,
    build_evaluation_profile,
    run_read_only_diagnosis,
)
from incidentpilot.llm.structured_output import OutputStrategy
from incidentpilot.observability.metrics import OperationalMetrics
from incidentpilot.observability.setup import create_meter_provider, create_tracer_provider
from incidentpilot.remediation.online import OnlineRemediationCoordinator
from incidentpilot.runtime.database import Database
from incidentpilot.runtime.heartbeat import ProcessHeartbeat
from incidentpilot.runtime.job_queue import ClaimedJob, PostgresJobQueue, SingleJobQueue
from incidentpilot.worker.main import run_worker
from incidentpilot.worker.processor import JobProcessor
from incidentpilot.worker.read_only import runtime_input_from_state


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bounded IncidentPilot read-only worker")
    parser.add_argument("--once", action="store_true", help="Claim and process at most one job")
    parser.add_argument("--job-id", help="Restrict this process to one known START job")
    parser.add_argument("--worker-id", default=f"read-only-{socket.gethostname()}")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("INCIDENTPILOT_WORKER_DATABASE_URL", DEFAULT_WORKER_DATABASE_URL),
    )
    parser.add_argument(
        "--telemetry-database-url",
        default=os.environ.get(
            "INCIDENTPILOT_TELEMETRY_DATABASE_URL", DEFAULT_TELEMETRY_DATABASE_URL
        ),
    )
    parser.add_argument("--model-profile", choices=("fast", "strong"), default="fast")
    parser.add_argument(
        "--structured-output-strategy",
        choices=("tool_strategy", "json_output"),
        default="tool_strategy",
    )
    args = parser.parse_args()
    asyncio.run(_serve(args))


async def _serve(args: argparse.Namespace) -> None:
    llm_settings = LlmSettings()
    if llm_settings.selected_api_key is None:
        raise RuntimeError(
            "Configure INCIDENTPILOT_LLM_API_KEY or the current provider key in the ignored .env"
        )
    profile = build_evaluation_profile(
        llm_settings,
        ModelSettings(),
        cast(Literal["fast", "strong"], args.model_profile),
    )
    database = Database(args.database_url)
    tracer_provider = create_tracer_provider("incidentpilot-read-only-worker")
    meter_provider = create_meter_provider("incidentpilot-read-only-worker")
    initial_state = DatabaseInitialStateLoader(database)
    operational_metrics = OperationalMetrics(meter_provider)

    async def handle(job: ClaimedJob) -> None:
        incident_id = job.incident_id
        if job.job_type == "RESUME":
            if not job.resume_reference_id:
                raise RuntimeError("remediation resume requires an approval reference")
            verifying_key = os.environ.get("INCIDENTPILOT_APPROVAL_VERIFYING_KEY", "")
            if not verifying_key:
                raise RuntimeError("approval verifying key is required for remediation")
            telemetry_database = Database(args.telemetry_database_url)
            try:
                await OnlineRemediationCoordinator(
                    worker_database=database,
                    telemetry_database=telemetry_database,
                    prometheus_url=os.environ.get(
                        "INCIDENTPILOT_PROMETHEUS_URL", "http://127.0.0.1:9090"
                    ),
                    action_mcp_url=os.environ.get(
                        "INCIDENTPILOT_ACTION_MCP_URL", "http://127.0.0.1:8102/mcp"
                    ),
                    approval_verifying_key=verifying_key,
                ).resume(
                    incident_id=incident_id,
                    approval_id=job.resume_reference_id,
                )
            finally:
                await telemetry_database.dispose()
            return
        state = await initial_state.load(incident_id)
        observation = TimeRange.model_validate(state["time_range"])
        await run_read_only_diagnosis(
            incident_id=incident_id,
            runtime_input=runtime_input_from_state(state),
            mode="multi",
            profile=profile,
            llm_settings=llm_settings,
            observation_started_at=observation.start,
            structured_output_strategy=cast(OutputStrategy, args.structured_output_strategy),
            tracer_provider=tracer_provider,
            operational_metrics=operational_metrics,
            worker_database_url=args.database_url,
            telemetry_database_url=args.telemetry_database_url,
            production_runtime=True,
        )

    queue = PostgresJobQueue(database)
    processor = JobProcessor(
        queue=SingleJobQueue(queue, args.job_id) if args.job_id else queue,
        worker_id=args.worker_id,
        handler=handle,
    )
    stop = asyncio.Event()
    heartbeat = ProcessHeartbeat(database, process_name="worker", instance_id=args.worker_id)
    await heartbeat.ready()
    heartbeat_task = asyncio.create_task(heartbeat.maintain(stop))
    try:
        if args.once:
            await processor.run_once()
        else:
            await run_worker(processor, stop=stop)
    finally:
        stop.set()
        await heartbeat_task
        await database.dispose()
        tracer_provider.shutdown()
        meter_provider.shutdown()


if __name__ == "__main__":
    main()
