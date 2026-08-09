from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any, Protocol, cast

from langgraph.types import Command
from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy.exc import SQLAlchemyError

from incidentpilot.observability.attributes import operation_span
from incidentpilot.observability.metrics import OperationalMetrics
from incidentpilot.orchestration.graph import graph_config
from incidentpilot.runtime.job_queue import ClaimedJob
from incidentpilot.worker.processor import JobProcessor


class GraphRunner(Protocol):
    async def aget_state(self, config: dict[str, Any]) -> Any: ...

    async def ainvoke(
        self,
        input: Any,
        config: dict[str, Any],
    ) -> dict[str, Any]: ...


class InitialStateLoader(Protocol):
    async def load(self, incident_id: str) -> dict[str, Any]: ...


class GraphResultSink(Protocol):
    async def persist(self, incident_id: str, state: dict[str, Any]) -> None: ...


class GraphRunIncomplete(RuntimeError):
    pass


class GraphJobHandler:
    def __init__(
        self,
        *,
        graph: GraphRunner,
        initial_state: InitialStateLoader,
        result_sink: GraphResultSink,
        tracer_provider: TracerProvider | None = None,
        operational_metrics: OperationalMetrics | None = None,
    ) -> None:
        self._graph = graph
        self._initial_state = initial_state
        self._result_sink = result_sink
        self._tracer_provider = tracer_provider
        self._operational_metrics = operational_metrics

    async def __call__(self, job: ClaimedJob) -> None:
        started = perf_counter()
        with operation_span(
            "incidentpilot.graph.run",
            attributes={"incidentpilot.job.type": job.job_type},
            provider=self._tracer_provider,
        ):
            try:
                config = cast(dict[str, Any], graph_config(job.incident_id))
                snapshot = await self._graph.aget_state(config)
                has_checkpoint = bool(snapshot.values)
                if job.job_type == "RESUME" and not has_checkpoint:
                    raise GraphRunIncomplete("resume job has no checkpoint")
                if job.job_type == "RESUME":
                    graph_input: Any = Command(resume=job.resume_reference_id)
                else:
                    graph_input = (
                        None if has_checkpoint else await self._initial_state.load(job.incident_id)
                    )
                result = await self._graph.ainvoke(graph_input, config)
                final_snapshot = await self._graph.aget_state(config)
                if final_snapshot.next:
                    if any(getattr(task, "interrupts", ()) for task in final_snapshot.tasks):
                        return
                    raise GraphRunIncomplete(
                        f"graph stopped before completion: {final_snapshot.next}"
                    )
                await self._result_sink.persist(job.incident_id, dict(result))
            except Exception:
                if self._operational_metrics is not None:
                    self._operational_metrics.record_agent(
                        "graph", int((perf_counter() - started) * 1000), success=False
                    )
                raise
        if self._operational_metrics is not None:
            self._operational_metrics.record_agent(
                "graph", int((perf_counter() - started) * 1000), success=True
            )


async def run_worker(
    processor: JobProcessor,
    *,
    stop: asyncio.Event,
    idle_seconds: float = 1.0,
) -> None:
    if idle_seconds <= 0:
        raise ValueError("idle_seconds must be positive")
    while not stop.is_set():
        try:
            handled = await processor.run_once()
        except SQLAlchemyError:
            handled = False
        if not handled:
            try:
                await asyncio.wait_for(stop.wait(), timeout=idle_seconds)
            except TimeoutError:
                continue
