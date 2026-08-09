from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic import BaseModel, Field

from incidentpilot.llm.gateway import (
    StructuredOutputError,
    StructuredOutputGateway,
)
from incidentpilot.llm.profiles import ModelProfile
from incidentpilot.llm.structured_output import (
    ModelInvocation,
    ModelTransport,
    RawModelResult,
)
from incidentpilot.llm.usage import ModelCallRecord, ModelCallRecorder
from incidentpilot.orchestration.state import SynthesisDraft


class Answer(BaseModel):
    value: int


class MappingAnswer(BaseModel):
    payload: dict[str, int]


class SupportedItem(BaseModel):
    evidence_ids: list[str] = Field(min_length=1)


class ScriptedTransport(ModelTransport):
    def __init__(self, script: Sequence[RawModelResult]) -> None:
        self.script = list(script)
        self.invocations: list[ModelInvocation] = []

    async def invoke(
        self,
        profile: ModelProfile,
        invocation: ModelInvocation,
        *,
        output_schema: type[BaseModel],
    ) -> RawModelResult:
        _ = profile, output_schema
        self.invocations.append(invocation)
        return self.script.pop(0)


class RecordingModelCalls(ModelCallRecorder):
    def __init__(self) -> None:
        self.records: list[ModelCallRecord] = []

    async def record(self, record: ModelCallRecord) -> None:
        self.records.append(record)


def _profile(*, native: bool | None, tools: bool = True) -> ModelProfile:
    return ModelProfile(
        name="test",
        provider="openai",
        model="runtime-model",
        base_url="https://api.example.test/v1",
        temperature=0,
        max_tokens=500,
        supports_tools=tools,
        supports_native_schema=native,
    )


def _invocation() -> ModelInvocation:
    return ModelInvocation(
        incident_id="inc-1",
        agent_name="commander",
        prompt_version="v1",
        system_prompt="Return typed output without hidden reasoning.",
        user_prompt="Return one integer.",
    )


@pytest.mark.asyncio
async def test_schema_failure_repairs_once_without_replaying_invalid_content() -> None:
    transport = ScriptedTransport(
        [
            RawModelResult(structured_output={"value": "invalid-secret-content"}),
            RawModelResult(structured_output={"value": 3}),
        ]
    )
    recorder = RecordingModelCalls()
    gateway = StructuredOutputGateway(transport=transport, recorder=recorder)

    result = await gateway.invoke(
        profile=_profile(native=True),
        invocation=_invocation(),
        output_schema=Answer,
    )

    assert result.value == 3
    assert len(transport.invocations) == 2
    assert transport.invocations[1].repair_instruction is not None
    assert "invalid-secret-content" not in transport.invocations[1].repair_instruction
    assert "Match the schema exactly" in transport.invocations[1].repair_instruction
    assert "Do not add wrapper or extra fields" in transport.invocations[1].repair_instruction
    assert [record.status for record in recorder.records] == [
        "SCHEMA_INVALID",
        "SUCCESS",
    ]


@pytest.mark.asyncio
async def test_json_output_repair_requests_json_instead_of_a_tool_call() -> None:
    transport = ScriptedTransport(
        [
            RawModelResult(structured_output={"extra": 1}),
            RawModelResult(structured_output={"value": 3}),
        ]
    )
    gateway = StructuredOutputGateway(
        transport=transport,
        recorder=RecordingModelCalls(),
    )

    result = await gateway.invoke(
        profile=_profile(native=False),
        invocation=_invocation(),
        output_schema=Answer,
        strategy="json_output",
    )

    assert result == Answer(value=3)
    assert transport.invocations[1].strategy == "json_output"
    repair = transport.invocations[1].repair_instruction
    assert repair is not None
    assert "Return one JSON object" in repair
    assert "tool" not in repair.lower()


@pytest.mark.asyncio
async def test_json_output_accepts_cross_service_application_failure_without_dependency() -> None:
    diagnosis = {
        "symptom_service": "frontend",
        "root_cause_service": "ad",
        "dependency_service": None,
        "root_cause_category": "application_failure",
        "root_cause_summary": "Ad service has an internal failure.",
        "confidence": 0.9,
        "evidence_ids": ["ev-metric", "ev-trace"],
        "customer_impact": "Ads fail.",
    }
    transport = ScriptedTransport(
        [
            RawModelResult(structured_output={"hypotheses": [], "diagnosis": diagnosis}),
        ]
    )
    recorder = RecordingModelCalls()
    gateway = StructuredOutputGateway(transport=transport, recorder=recorder)

    result = await gateway.invoke(
        profile=_profile(native=False),
        invocation=_invocation(),
        output_schema=SynthesisDraft,
        strategy="json_output",
    )

    assert result.diagnosis is not None
    assert result.diagnosis.root_cause_category == "application_failure"
    assert [record.status for record in recorder.records] == ["SUCCESS"]


@pytest.mark.asyncio
async def test_repair_keeps_required_fields_and_drops_invalid_optional_list_items() -> None:
    transport = ScriptedTransport(
        [
            RawModelResult(structured_output={"evidence_ids": []}),
            RawModelResult(structured_output={"evidence_ids": ["ev-1"]}),
        ]
    )
    gateway = StructuredOutputGateway(
        transport=transport,
        recorder=RecordingModelCalls(),
    )

    result = await gateway.invoke(
        profile=_profile(native=False),
        invocation=_invocation(),
        output_schema=SupportedItem,
        strategy="json_output",
    )

    assert result == SupportedItem(evidence_ids=["ev-1"])
    repair = transport.invocations[1].repair_instruction
    assert repair is not None
    assert "Do not remove required fields" in repair
    assert "remove the entire optional list item" in repair
    assert "remove every field named in the errors" not in repair


@pytest.mark.asyncio
@pytest.mark.parametrize("container", ["answer", "output", "result", "parameters", "value"])
async def test_single_output_wrapper_is_unwrapped_before_strict_validation(
    container: str,
) -> None:
    gateway = StructuredOutputGateway(
        transport=ScriptedTransport([RawModelResult(structured_output={container: {"value": 5}})]),
        recorder=RecordingModelCalls(),
    )

    result = await gateway.invoke(
        profile=_profile(native=True),
        invocation=_invocation(),
        output_schema=Answer,
    )

    assert result == Answer(value=5)


@pytest.mark.asyncio
async def test_valid_single_mapping_field_is_validated_before_wrapper_fallback() -> None:
    gateway = StructuredOutputGateway(
        transport=ScriptedTransport([RawModelResult(structured_output={"payload": {"count": 2}})]),
        recorder=RecordingModelCalls(),
    )

    result = await gateway.invoke(
        profile=_profile(native=True),
        invocation=_invocation(),
        output_schema=MappingAnswer,
    )

    assert result == MappingAnswer(payload={"count": 2})


@pytest.mark.asyncio
async def test_persistent_invalid_schema_stops_after_two_repairs() -> None:
    recorder = RecordingModelCalls()
    gateway = StructuredOutputGateway(
        transport=ScriptedTransport(
            [RawModelResult(structured_output={"wrong": True}) for _ in range(3)]
        ),
        recorder=recorder,
    )

    with pytest.raises(StructuredOutputError, match="three attempts.*value:missing"):
        await gateway.invoke(
            profile=_profile(native=True),
            invocation=_invocation(),
            output_schema=Answer,
        )

    assert len(recorder.records) == 3
    assert all(record.status == "SCHEMA_INVALID" for record in recorder.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, None])
async def test_unknown_or_unsupported_native_schema_uses_tool_strategy(
    native: bool | None,
) -> None:
    transport = ScriptedTransport(
        [
            RawModelResult(
                structured_output={"value": 5},
                tool_name="submit_structured_output",
            )
        ]
    )
    gateway = StructuredOutputGateway(
        transport=transport,
        recorder=RecordingModelCalls(),
    )

    result = await gateway.invoke(
        profile=_profile(native=native),
        invocation=_invocation(),
        output_schema=Answer,
    )

    assert result.value == 5
    assert transport.invocations[0].strategy == "tool_strategy"


@pytest.mark.asyncio
async def test_tool_strategy_rejects_wrong_tool_name_or_missing_tool_support() -> None:
    gateway = StructuredOutputGateway(
        transport=ScriptedTransport(
            [
                RawModelResult(
                    structured_output={"value": 1},
                    tool_name="other_tool",
                )
                for _ in range(3)
            ]
        ),
        recorder=RecordingModelCalls(),
    )
    with pytest.raises(StructuredOutputError, match="three attempts"):
        await gateway.invoke(
            profile=_profile(native=False),
            invocation=_invocation(),
            output_schema=Answer,
        )

    with pytest.raises(ValueError, match="structured output"):
        await gateway.invoke(
            profile=_profile(native=False, tools=False),
            invocation=_invocation(),
            output_schema=Answer,
        )
