from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field

from incidentpilot.domain import DomainModel
from incidentpilot.incidents.models import ModelCallRow
from incidentpilot.runtime.database import Database

ModelCallStatus = Literal[
    "SUCCESS",
    "SCHEMA_INVALID",
    "RATE_LIMITED",
    "TIMEOUT",
    "CONNECTION_ERROR",
    "PROVIDER_ERROR",
]


class ModelUsage(DomainModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_microusd: int = Field(default=0, ge=0)
    usage_missing: bool = False


class ModelCallRecord(DomainModel):
    call_id: str = Field(min_length=1, max_length=64)
    incident_id: str = Field(min_length=1, max_length=64)
    agent_name: str = Field(min_length=1, max_length=100)
    model_profile: str = Field(min_length=1, max_length=100)
    prompt_version: str = Field(min_length=1, max_length=100)
    strategy: Literal["native_schema", "tool_strategy", "json_output"]
    attempt: int = Field(ge=1, le=3)
    status: ModelCallStatus
    structured_response: dict[str, Any] | None = None
    usage: ModelUsage
    latency_ms: int = Field(ge=0)
    error_summary: str | None = Field(default=None, max_length=500)


class ModelCallRecorder(Protocol):
    async def record(self, record: ModelCallRecord) -> None: ...


class SqlAlchemyModelCallRecorder:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def record(self, record: ModelCallRecord) -> None:
        async with self._database.session_factory() as session, session.begin():
            session.add(
                ModelCallRow(
                    id=record.call_id,
                    incident_id=record.incident_id,
                    agent_name=record.agent_name,
                    model_profile=record.model_profile,
                    prompt_version=record.prompt_version,
                    input_tokens=record.usage.input_tokens,
                    output_tokens=record.usage.output_tokens,
                    cost_microusd=record.usage.cost_microusd,
                    duration_ms=record.latency_ms,
                    status=record.status,
                )
            )


def estimate_cost_microusd(
    *,
    input_tokens: int,
    output_tokens: int,
    input_usd_per_million: float,
    output_usd_per_million: float,
) -> int:
    usd = (
        input_tokens * input_usd_per_million + output_tokens * output_usd_per_million
    ) / 1_000_000
    return round(usd * 1_000_000)
