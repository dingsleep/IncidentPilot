from __future__ import annotations

import json
from collections.abc import Sequence

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel

from incidentpilot.llm.gateway import (
    ModelRateLimitError,
    ModelTimeoutError,
    OpenAICompatibleChatTransport,
    StructuredOutputGateway,
)
from incidentpilot.llm.profiles import (
    ModelProfile,
    ModelProvider,
    build_model_profiles,
)
from incidentpilot.llm.structured_output import (
    ModelInvocation,
    ModelTransport,
    RawModelResult,
)
from incidentpilot.llm.usage import ModelCallRecord, ModelCallRecorder, ModelUsage
from scripts.benchmark_models import render_report, tool_probe


class Answer(BaseModel):
    value: int


class ScriptedTransport(ModelTransport):
    def __init__(self, script: Sequence[RawModelResult | Exception]) -> None:
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
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RecordingModelCalls(ModelCallRecorder):
    def __init__(self) -> None:
        self.records: list[ModelCallRecord] = []

    async def record(self, record: ModelCallRecord) -> None:
        self.records.append(record)


def _profile(
    *,
    native: bool | None = True,
    tools: bool = True,
    provider: ModelProvider = "openai",
) -> ModelProfile:
    return ModelProfile(
        name="test",
        provider=provider,
        model="configured-at-runtime",
        base_url="https://api.example.test/v1",
        temperature=0,
        max_tokens=500,
        supports_tools=tools,
        supports_native_schema=native,
    )


def _invocation() -> ModelInvocation:
    return ModelInvocation(
        incident_id="inc-1",
        agent_name="triage",
        prompt_version="v1",
        system_prompt="Return the requested typed result.",
        user_prompt="Return value 7.",
    )


@pytest.mark.asyncio
async def test_gateway_records_valid_output_and_missing_usage_without_private_content() -> None:
    transport = ScriptedTransport([RawModelResult(structured_output={"value": 7})])
    recorder = RecordingModelCalls()
    gateway = StructuredOutputGateway(transport=transport, recorder=recorder)

    result = await gateway.invoke(
        profile=_profile(),
        invocation=_invocation(),
        output_schema=Answer,
    )

    assert result == Answer(value=7)
    assert recorder.records[0].usage == ModelUsage(
        input_tokens=0,
        output_tokens=0,
        cost_microusd=0,
        usage_missing=True,
    )
    assert recorder.records[0].prompt_version == "v1"
    persisted = recorder.records[0].model_dump()
    assert "system_prompt" not in persisted
    assert "user_prompt" not in persisted
    assert "chain_of_thought" not in persisted
    assert persisted["structured_response"] == {"value": 7}


@pytest.mark.asyncio
async def test_gateway_retries_rate_limit_and_timeout_as_separate_attempts() -> None:
    usage = ModelUsage(input_tokens=10, output_tokens=3, cost_microusd=2)
    recorder = RecordingModelCalls()
    gateway = StructuredOutputGateway(
        transport=ScriptedTransport(
            [
                ModelRateLimitError("rate limited"),
                ModelTimeoutError("timed out"),
                RawModelResult(structured_output={"value": 9}, usage=usage),
            ]
        ),
        recorder=recorder,
    )

    result = await gateway.invoke(
        profile=_profile(),
        invocation=_invocation(),
        output_schema=Answer,
    )

    assert result.value == 9
    assert [record.status for record in recorder.records] == [
        "RATE_LIMITED",
        "TIMEOUT",
        "SUCCESS",
    ]
    assert recorder.records[-1].usage == usage


def test_profiles_are_runtime_configured_for_strong_fast_and_local() -> None:
    profiles = build_model_profiles(
        provider="openai",
        base_url="https://api.example.test/v1",
        strong_model="strong-runtime-model",
        fast_model="fast-runtime-model",
        local_model="local-runtime-model",
        local_base_url="http://127.0.0.1:11434/v1",
    )

    assert profiles.strong.model == "strong-runtime-model"
    assert profiles.fast.model == "fast-runtime-model"
    assert profiles.local.provider == "openai-compatible"
    assert profiles.local.base_url == "http://127.0.0.1:11434/v1"
    assert "api_key" not in profiles.model_dump_json()


@pytest.mark.asyncio
async def test_deepseek_transport_uses_chat_tool_strategy_and_ignores_reasoning() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": "private reasoning must be ignored",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_structured_output",
                                        "arguments": '{"value": 11}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 4,
                    "total_tokens": 16,
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        api_key="test",
        base_url="https://api.deepseek.com",
        http_client=http_client,
        max_retries=0,
    )
    transport = OpenAICompatibleChatTransport(client)
    try:
        result = await transport.invoke(
            _profile(native=False, provider="deepseek"),
            _invocation().model_copy(update={"strategy": "tool_strategy"}),
            output_schema=Answer,
        )
    finally:
        await transport.aclose()

    assert result.structured_output == {"value": 11}
    assert result.usage == ModelUsage(input_tokens=12, output_tokens=4)
    assert captured["model"] == "configured-at-runtime"
    assert captured["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_structured_output"},
    }
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert tools[0]["function"]["description"] == (
        "You must call this tool exactly once with the requested structured result. "
        "Do not return ordinary assistant text."
    )
    assert captured["thinking"] == {"type": "disabled"}
    assert "reasoning_content" not in result.model_dump_json()
    assert client.is_closed


@pytest.mark.asyncio
async def test_deepseek_transport_uses_json_output_without_tools() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-json-1",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"value": 13}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        api_key="test",
        base_url="https://api.deepseek.com",
        http_client=http_client,
        max_retries=0,
    )
    transport = OpenAICompatibleChatTransport(client)
    try:
        result = await transport.invoke(
            _profile(native=False, provider="deepseek"),
            _invocation().model_copy(update={"strategy": "json_output"}),
            output_schema=Answer,
        )
    finally:
        await transport.aclose()

    assert result.structured_output == {"value": 13}
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["thinking"] == {"type": "disabled"}
    assert "tools" not in captured
    assert "tool_choice" not in captured
    messages = captured["messages"]
    assert isinstance(messages, list)
    assert "JSON Schema" in messages[0]["content"]


@pytest.mark.asyncio
async def test_qwen_json_output_disables_thinking_and_omits_token_cap() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-qwen-json-1",
                "object": "chat.completion",
                "created": 1,
                "model": "qwen3.6-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"value": 13}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        api_key="test",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        http_client=http_client,
        max_retries=0,
    )
    transport = OpenAICompatibleChatTransport(client)
    try:
        result = await transport.invoke(
            _profile(native=False, provider="qwen").model_copy(update={"model": "qwen3.6-flash"}),
            _invocation().model_copy(update={"strategy": "json_output"}),
            output_schema=Answer,
        )
    finally:
        await transport.aclose()

    assert result.structured_output == {"value": 13}
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["enable_thinking"] is False
    assert "max_tokens" not in captured


@pytest.mark.asyncio
async def test_deepseek_tool_probe_disables_thinking_before_forcing_tool_choice() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-2",
                                    "type": "function",
                                    "function": {
                                        "name": "query_metrics",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 2,
                    "total_tokens": 10,
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        api_key="test",
        base_url="https://api.deepseek.com",
        http_client=http_client,
        max_retries=0,
    )
    try:
        passed, _, _ = await tool_probe(
            client=client,
            profile=_profile(native=False, provider="deepseek"),
            parallel=False,
        )
    finally:
        await client.close()

    assert passed is True
    assert captured["tool_choice"] == "required"
    assert captured["thinking"] == {"type": "disabled"}


def test_benchmark_uses_profile_specific_prices() -> None:
    report = render_report(
        [
            ("strong", "schema", True, 100, ModelUsage(input_tokens=1_000_000, output_tokens=0)),
            ("fast", "schema", True, 100, ModelUsage(input_tokens=1_000_000, output_tokens=0)),
        ],
        models={
            "strong": "deepseek-v4-pro",
            "fast": "deepseek-v4-flash",
        },
        prices={
            "strong": (0.435, 0.87),
            "fast": (0.14, 0.28),
        },
    )

    assert "| strong | schema | True | 100 | 1000000 | 0 | 0.435000 |" in report
    assert "| fast | schema | True | 100 | 1000000 | 0 | 0.140000 |" in report
    assert (
        "| strong | `deepseek-v4-pro` | 1/1 (100%) | 100 | 100 | 1000000 | 0 | 0.435000 |"
    ) in report
    assert (
        "| fast | `deepseek-v4-flash` | 1/1 (100%) | 100 | 100 | 1000000 | 0 | 0.140000 |"
    ) in report
