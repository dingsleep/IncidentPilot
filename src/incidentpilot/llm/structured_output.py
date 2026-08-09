from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel

from incidentpilot.domain import DomainModel
from incidentpilot.llm.profiles import ModelProfile
from incidentpilot.llm.usage import ModelUsage

OutputStrategy = Literal["native_schema", "tool_strategy", "json_output"]


class ModelInvocation(DomainModel):
    incident_id: str
    agent_name: str
    prompt_version: str
    system_prompt: str
    user_prompt: str
    strategy: OutputStrategy = "native_schema"
    repair_instruction: str | None = None


class RawModelResult(DomainModel):
    structured_output: dict[str, Any] | None = None
    tool_name: str | None = None
    usage: ModelUsage | None = None


class ModelTransport(Protocol):
    async def invoke(
        self,
        profile: ModelProfile,
        invocation: ModelInvocation,
        *,
        output_schema: type[BaseModel],
    ) -> RawModelResult: ...
