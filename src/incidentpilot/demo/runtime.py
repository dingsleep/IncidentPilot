from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import select

from incidentpilot.domain import DomainModel
from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.evaluation.cli import drive_otel_demo_traffic
from incidentpilot.evaluation.isolation import FlagdScenarioController, episode_environment_lock
from incidentpilot.evaluation.loader import LoadedEpisode, load_episode_suite
from incidentpilot.incidents.models import AlertRow, ChangeEventRow
from incidentpilot.incidents.progress import IncidentProgressRecorder
from incidentpilot.remediation.adapters.flagd import FlagdChangeMapping
from incidentpilot.remediation.private_mappings import (
    PrivateMappingCipher,
    SqlAlchemyPrivateMappingRepository,
)
from incidentpilot.runtime.database import Database

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
TERMINAL_STATUSES = frozenset(
    {
        IncidentStatus.RESOLVED,
        IncidentStatus.RESOLVED_READ_ONLY,
        IncidentStatus.NEEDS_HUMAN,
        IncidentStatus.POLICY_REJECTED,
        IncidentStatus.ACTION_FAILED,
        IncidentStatus.REJECTED,
    }
)
PUBLIC_SCENARIOS = frozenset(
    {"payment-unreachable-001", "cart-failure-001", "recommendation-cache-leak-001"}
)


class DemoRunRequest(DomainModel):
    incident_id: str
    scenario_id: Literal[
        "payment-unreachable-001",
        "cart-failure-001",
        "recommendation-cache-leak-001",
    ]


class DemoRunAccepted(DomainModel):
    incident_id: str
    scenario_id: str
    status: Literal["preparing"] = "preparing"
    real_execution: bool = True


class DemoRuntime:
    def __init__(self) -> None:
        self.database = Database(
            os.environ.get(
                "INCIDENTPILOT_WORKER_DATABASE_URL",
                "postgresql+asyncpg://graph_worker_role:worker-local-only@127.0.0.1:5433/incidentpilot",
            )
        )
        self.api_url = os.environ.get("INCIDENTPILOT_API_URL", "http://127.0.0.1:8200")
        self.evaluation_database = Database(
            os.environ.get(
                "INCIDENTPILOT_EVALUATION_DATABASE_URL",
                "postgresql+asyncpg://evaluation_role:evaluation-local-only@127.0.0.1:5433/incidentpilot",
            )
        )
        mapping_key = os.environ.get("INCIDENTPILOT_PRIVATE_MAPPING_ENCRYPTION_KEY", "")
        self.private_mappings = (
            SqlAlchemyPrivateMappingRepository(
                database=self.evaluation_database,
                cipher=PrivateMappingCipher.from_base64(mapping_key),
            )
            if mapping_key
            else None
        )
        self.flagd_url = os.environ.get(
            "INCIDENTPILOT_DEMO_FLAGD_API_URL", "http://127.0.0.1:4000/api"
        )
        self.storefront_url = os.environ.get(
            "INCIDENTPILOT_DEMO_STOREFRONT_URL", "http://127.0.0.1:8080"
        )
        self._active: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._environment_lock = asyncio.Lock()

    async def close(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.database.dispose()
        await self.evaluation_database.dispose()

    def start(self, request: DemoRunRequest) -> None:
        if request.incident_id in self._active:
            raise ValueError("this incident already has an active demo run")
        self._active.add(request.incident_id)
        task = asyncio.create_task(self._run(request))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, request: DemoRunRequest) -> None:
        recorder = IncidentProgressRecorder(
            self.database,
            incident_id=request.incident_id,
            actor_id="demo-runner",
        )
        try:
            episode = _public_episode(request.scenario_id)
            injection = episode.execution.injections[0]
            traffic = episode.execution.traffic
            if traffic is None:
                raise ValueError("a public fault demo must define traffic")
            async with self._environment_lock:
                await recorder.emit(
                    "incident.status_changed",
                    stage="intake",
                    status="running",
                    message="正在准备隔离的真实微服务故障环境",
                    details={
                        "scenario_id": request.scenario_id,
                        "environment": "OpenTelemetry Demo 2.2.0",
                        "mock": False,
                    },
                )
                with (
                    httpx.Client(timeout=20, trust_env=False) as client,
                    episode_environment_lock(),
                ):
                    controller = FlagdScenarioController(client=client, base_url=self.flagd_url)
                    with controller.activate(injection.scenario_key, injection.variant) as original:
                        await self._record_public_change(
                            request=request,
                            target_service=episode.public_input.alert.service_hint or "unknown",
                            flag_name=injection.scenario_key,
                            restore_config=original.config,
                            restore_digest=original.digest,
                        )
                        await recorder.emit(
                            "stage.completed",
                            stage="intake",
                            status="completed",
                            message="受控故障已生效，正在生成真实请求与遥测",
                            details={"telemetry_source": "OpenTelemetry Demo", "mock": False},
                        )
                        await asyncio.to_thread(
                            drive_otel_demo_traffic,
                            client,
                            traffic,
                            base_url=self.storefront_url,
                            settle_seconds=2,
                        )
                        await recorder.emit(
                            "evidence.created",
                            stage="intake",
                            status="completed",
                            message="真实故障流量已产生，等待遥测管道完成索引",
                            details={"traffic_operation": traffic.operation},
                        )
                        await asyncio.sleep(12)
                        await self._start_analysis(request.incident_id)
                        await self._wait_for_terminal(request.incident_id)
                await recorder.emit(
                    "verification.completed",
                    stage="verification",
                    status="completed",
                    message="演示隔离环境已恢复到运行前快照",
                    details={"environment_restored": True, "remediation_action": False},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await recorder.emit(
                "run.failed",
                stage="intake",
                status="failed",
                message="真实演示运行失败，隔离环境将自动恢复",
                details={"error_type": type(exc).__name__},
            )
        finally:
            self._active.discard(request.incident_id)

    async def _start_analysis(self, incident_id: str) -> None:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.post(
                f"{self.api_url}/api/v1/incidents/{incident_id}/analysis",
                headers={"X-IncidentPilot-Actor": "local-operator"},
            )
            response.raise_for_status()

    async def _wait_for_terminal(self, incident_id: str) -> None:
        deadline = asyncio.get_running_loop().time() + 1800
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            while asyncio.get_running_loop().time() < deadline:
                response = await client.get(
                    f"{self.api_url}/api/v1/incidents/{incident_id}",
                    headers={"X-IncidentPilot-Actor": "local-viewer"},
                )
                response.raise_for_status()
                if IncidentStatus(response.json()["status"]) in TERMINAL_STATUSES:
                    return
                await asyncio.sleep(2)
        raise TimeoutError("demo diagnosis did not reach a terminal state")

    async def _record_public_change(
        self,
        *,
        request: DemoRunRequest,
        target_service: str,
        flag_name: str,
        restore_config: dict[str, object],
        restore_digest: str,
    ) -> None:
        if self.private_mappings is None:
            raise RuntimeError("private mapping encryption is not configured")
        change_id = f"chg_demo_{request.incident_id.removeprefix('inc_')}"
        async with self.evaluation_database.session_factory() as session, session.begin():
            existing = await session.get(ChangeEventRow, change_id)
            if existing is None:
                session.add(
                    ChangeEventRow(
                        id=change_id,
                        service=target_service,
                        change_type="configuration",
                        summary=f"{target_service} 检测到近期受控配置变更",
                        occurred_at=datetime.now(UTC),
                    )
                )
            alert = await session.scalar(
                select(AlertRow).where(AlertRow.incident_id == request.incident_id).limit(1)
            )
            if alert is None:
                raise LookupError("demo incident alert was not found")
            payload = dict(alert.payload_json)
            labels = dict(payload.get("labels") or {})
            labels["change_id"] = change_id
            labels["controlled_demo"] = "true"
            payload["labels"] = labels
            alert.payload_json = payload
        await self.private_mappings.store(
            FlagdChangeMapping(
                change_id=change_id,
                target_service=target_service,
                flag_name=flag_name,
                restore_config=restore_config,
                restore_digest=restore_digest,
            )
        )


def _public_episode(scenario_id: str) -> LoadedEpisode:
    if scenario_id not in PUBLIC_SCENARIOS:
        raise ValueError("unknown public demo scenario")
    from pathlib import Path

    root = Path(ROOT)
    episodes = load_episode_suite(root / "scenarios", root / "service_catalog" / "otel-demo.yaml")
    return next(item for item in episodes if item.id == scenario_id)


def create_demo_app() -> FastAPI:
    runtime = DemoRuntime()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.demo_runtime = runtime
        yield
        await runtime.close()

    app = FastAPI(title="IncidentPilot Local Demo Runner", lifespan=lifespan)

    @app.get("/health/ready")
    async def ready() -> dict[str, bool]:  # pyright: ignore[reportUnusedFunction]
        return {"ready": True}

    @app.post("/runs", response_model=DemoRunAccepted, status_code=202)
    async def start_run(  # pyright: ignore[reportUnusedFunction]
        request: DemoRunRequest,
    ) -> DemoRunAccepted:
        try:
            runtime.start(request)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return DemoRunAccepted(incident_id=request.incident_id, scenario_id=request.scenario_id)

    return app


app = create_demo_app()
