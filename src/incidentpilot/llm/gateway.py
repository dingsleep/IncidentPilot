from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from openai.types.chat import ChatCompletionMessageFunctionToolCall
from opentelemetry.sdk.trace import TracerProvider
from pydantic import BaseModel, ValidationError

from incidentpilot.config import LlmSettings
from incidentpilot.llm.profiles import ModelProfile
from incidentpilot.llm.structured_output import (
    ModelInvocation,
    ModelTransport,
    OutputStrategy,
    RawModelResult,
)
from incidentpilot.llm.usage import (
    ModelCallRecord,
    ModelCallRecorder,
    ModelCallStatus,
    ModelUsage,
)
from incidentpilot.observability.attributes import operation_span
from incidentpilot.observability.genai_semconv import record_genai_call
from incidentpilot.observability.metrics import OperationalMetrics

Sleeper = Callable[[float], Awaitable[None]]
SUBMIT_TOOL_NAME = "submit_structured_output"
STRUCTURED_OUTPUT_VERSION = "v2"
PROVIDER_TIMEOUT_SECONDS = 90.0
PROVIDER_CONNECTION_RETRIES = 2


class StructuredOutputError(RuntimeError):
    pass


class ModelRateLimitError(RuntimeError):
    pass


class ModelTimeoutError(RuntimeError):
    pass


class ModelConnectionError(RuntimeError):
    pass


class ModelProviderError(RuntimeError):
    pass


class StructuredOutputGateway:
    def __init__(
        self,
        *,
        transport: ModelTransport,
        recorder: ModelCallRecorder,
        retry_backoff_seconds: float = 0.05,
        sleeper: Sleeper = asyncio.sleep,
        tracer_provider: TracerProvider | None = None,
        operational_metrics: OperationalMetrics | None = None,
    ) -> None:
        self._transport = transport
        self._recorder = recorder
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleeper = sleeper
        self._tracer_provider = tracer_provider
        self._operational_metrics = operational_metrics

    async def invoke[OutputT: BaseModel](
        self,
        *,
        profile: ModelProfile,
        invocation: ModelInvocation,
        output_schema: type[OutputT],
        strategy: OutputStrategy | None = None,
    ) -> OutputT:
        selected_strategy = strategy or _strategy(profile)
        repair_instruction: str | None = None
        for attempt in range(1, 4):
            current = invocation.model_copy(
                update={
                    "strategy": selected_strategy,
                    "repair_instruction": repair_instruction,
                }
            )
            started = perf_counter()
            try:
                with operation_span(
                    "incidentpilot.model.invoke",
                    attributes={"incidentpilot.agent.name": current.agent_name},
                    provider=self._tracer_provider,
                ) as span:
                    raw = await self._transport.invoke(
                        profile,
                        current,
                        output_schema=output_schema,
                    )
                    usage = raw.usage or _missing_usage()
                    record_genai_call(
                        span,
                        workflow_name=current.agent_name,
                        model=profile.model,
                        prompt=f"{current.system_prompt}\n{current.user_prompt}",
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                    )
                    if self._operational_metrics is not None:
                        self._operational_metrics.record_agent(
                            current.agent_name,
                            int((perf_counter() - started) * 1000),
                            success=True,
                        )
                        self._operational_metrics.record_model(
                            agent_name=current.agent_name,
                            model=profile.model,
                            input_tokens=usage.input_tokens,
                            output_tokens=usage.output_tokens,
                            cost_microusd=usage.cost_microusd,
                        )
            except ModelRateLimitError:
                await self._record_failure(
                    profile,
                    current,
                    attempt,
                    "RATE_LIMITED",
                    started,
                    "provider rate limit",
                )
                if attempt == 3:
                    raise
                await self._backoff(attempt)
                continue
            except ModelTimeoutError:
                await self._record_failure(
                    profile,
                    current,
                    attempt,
                    "TIMEOUT",
                    started,
                    "provider timeout",
                )
                if attempt == 3:
                    raise
                await self._backoff(attempt)
                continue
            except ModelConnectionError:
                await self._record_failure(
                    profile,
                    current,
                    attempt,
                    "CONNECTION_ERROR",
                    started,
                    "provider connection error",
                )
                if attempt == 3:
                    raise
                await self._backoff(attempt)
                continue
            except Exception as exc:
                await self._record_failure(
                    profile,
                    current,
                    attempt,
                    "PROVIDER_ERROR",
                    started,
                    type(exc).__name__,
                )
                raise ModelProviderError("model provider call failed") from exc

            try:
                if selected_strategy == "tool_strategy" and raw.tool_name != SUBMIT_TOOL_NAME:
                    raise ValueError("model did not call the structured output tool")
                if raw.structured_output is None:
                    raise ValueError("model returned no structured output")
                parsed = _validate_structured_output(raw.structured_output, output_schema)
            except (ValidationError, ValueError) as exc:
                repair_instruction = _repair_instruction(exc, selected_strategy)
                await self._recorder.record(
                    _record(
                        profile=profile,
                        invocation=current,
                        attempt=attempt,
                        status="SCHEMA_INVALID",
                        started=started,
                        structured_response=None,
                        usage=raw.usage or _missing_usage(),
                        error_summary=repair_instruction,
                    )
                )
                continue

            await self._recorder.record(
                _record(
                    profile=profile,
                    invocation=current,
                    attempt=attempt,
                    status="SUCCESS",
                    started=started,
                    structured_response=parsed.model_dump(mode="json"),
                    usage=raw.usage or _missing_usage(),
                    error_summary=None,
                )
            )
            return parsed
        detail = f": {repair_instruction}" if repair_instruction else ""
        raise StructuredOutputError(f"structured output failed after three attempts{detail}")

    async def _record_failure(
        self,
        profile: ModelProfile,
        invocation: ModelInvocation,
        attempt: int,
        status: ModelCallStatus,
        started: float,
        error_summary: str,
    ) -> None:
        await self._recorder.record(
            _record(
                profile=profile,
                invocation=invocation,
                attempt=attempt,
                status=status,
                started=started,
                structured_response=None,
                usage=_missing_usage(),
                error_summary=error_summary,
            )
        )

    async def _backoff(self, attempt: int) -> None:
        await self._sleeper(self._retry_backoff_seconds * 2 ** (attempt - 1))


class OpenAICompatibleChatTransport:
    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: LlmSettings) -> OpenAICompatibleChatTransport:
        api_key = settings.selected_api_key
        if api_key is None:
            raise ValueError("LLM API key is not configured")
        return cls(
            AsyncOpenAI(
                api_key=api_key.get_secret_value(),
                base_url=settings.base_url,
                timeout=PROVIDER_TIMEOUT_SECONDS,
                max_retries=PROVIDER_CONNECTION_RETRIES,
            )
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def invoke(
        self,
        profile: ModelProfile,
        invocation: ModelInvocation,
        *,
        output_schema: type[BaseModel],
    ) -> RawModelResult:
        user_prompt = invocation.user_prompt
        if invocation.repair_instruction:
            user_prompt = f"{user_prompt}\n\n{invocation.repair_instruction}"
        system_prompt = invocation.system_prompt
        if invocation.strategy == "json_output":
            schema = json.dumps(
                output_schema.model_json_schema(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            system_prompt = (
                f"{system_prompt}\n\nReturn one JSON object matching this JSON Schema exactly. "
                f"Do not add wrapper or extra fields.\nJSON Schema:\n{schema}"
            )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            if invocation.strategy == "native_schema":
                response = await self._client.chat.completions.parse(
                    model=profile.model,
                    messages=cast(Any, messages),
                    response_format=output_schema,
                    temperature=profile.temperature,
                    max_tokens=profile.max_tokens,
                )
                parsed = response.choices[0].message.parsed
                return RawModelResult(
                    structured_output=parsed.model_dump(mode="json") if parsed else None,
                    usage=_chat_usage(response.usage),
                )
            if invocation.strategy == "json_output":
                if profile.provider == "qwen":
                    response = await self._client.chat.completions.create(
                        model=profile.model,
                        messages=cast(Any, messages),
                        temperature=profile.temperature,
                        response_format={"type": "json_object"},
                        extra_body=tool_strategy_extra_body(profile),
                    )
                else:
                    response = await self._client.chat.completions.create(
                        model=profile.model,
                        messages=cast(Any, messages),
                        temperature=profile.temperature,
                        max_tokens=profile.max_tokens,
                        response_format={"type": "json_object"},
                        extra_body=tool_strategy_extra_body(profile),
                    )
                content = response.choices[0].message.content
                try:
                    raw_content: Any = json.loads(content) if content else None
                except json.JSONDecodeError:
                    raw_content = None
                structured: dict[str, Any] | None = None
                if isinstance(raw_content, dict):
                    raw_mapping = cast(dict[Any, Any], raw_content)
                    structured = {str(key): value for key, value in raw_mapping.items()}
                return RawModelResult(
                    structured_output=structured,
                    usage=_chat_usage(response.usage),
                )
            response = await self._client.chat.completions.create(
                model=profile.model,
                messages=cast(Any, messages),
                temperature=profile.temperature,
                max_tokens=profile.max_tokens,
                tools=[
                    cast(
                        Any,
                        {
                            "type": "function",
                            "function": {
                                "name": SUBMIT_TOOL_NAME,
                                "description": (
                                    "You must call this tool exactly once with the requested "
                                    "structured result. Do not return ordinary assistant text."
                                ),
                                "parameters": output_schema.model_json_schema(),
                            },
                        },
                    )
                ],
                tool_choice={
                    "type": "function",
                    "function": {"name": SUBMIT_TOOL_NAME},
                },
                extra_body=tool_strategy_extra_body(profile),
            )
        except RateLimitError as exc:
            raise ModelRateLimitError("provider rate limit") from exc
        except APITimeoutError as exc:
            raise ModelTimeoutError("provider timeout") from exc
        except APIConnectionError as exc:
            raise ModelConnectionError("provider connection error") from exc
        message = response.choices[0].message
        tool_call = next(iter(message.tool_calls or []), None)
        if not isinstance(tool_call, ChatCompletionMessageFunctionToolCall):
            return RawModelResult(usage=_chat_usage(response.usage))
        try:
            raw_arguments: Any = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            raw_arguments = None
        arguments: dict[str, Any] | None = None
        if isinstance(raw_arguments, dict):
            raw_mapping = cast(dict[Any, Any], raw_arguments)
            arguments = {str(key): value for key, value in raw_mapping.items()}
        return RawModelResult(
            structured_output=arguments,
            tool_name=tool_call.function.name,
            usage=_chat_usage(response.usage),
        )


def _strategy(profile: ModelProfile) -> OutputStrategy:
    if profile.supports_native_schema is True:
        return "native_schema"
    if profile.supports_tools:
        return "tool_strategy"
    raise ValueError("profile cannot produce structured output")


def tool_strategy_extra_body(profile: ModelProfile) -> dict[str, Any] | None:
    if profile.provider == "deepseek":
        return {"thinking": {"type": "disabled"}}
    if profile.provider == "qwen":
        return {"enable_thinking": False}
    return None


def _record(
    *,
    profile: ModelProfile,
    invocation: ModelInvocation,
    attempt: int,
    status: ModelCallStatus,
    started: float,
    structured_response: dict[str, Any] | None,
    usage: ModelUsage,
    error_summary: str | None,
) -> ModelCallRecord:
    return ModelCallRecord(
        call_id=f"mc_{uuid4().hex}",
        incident_id=invocation.incident_id,
        agent_name=invocation.agent_name,
        model_profile=profile.name,
        prompt_version=invocation.prompt_version,
        strategy=invocation.strategy,
        attempt=attempt,
        status=status,
        structured_response=structured_response,
        usage=usage,
        latency_ms=int((perf_counter() - started) * 1000),
        error_summary=error_summary,
    )


def _repair_instruction(
    exc: ValidationError | ValueError,
    strategy: OutputStrategy,
) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_input=False, include_url=False)[:5]
        failures = [
            f"{'.'.join(str(part) for part in error['loc'])}:{error['type']}" for error in errors
        ]
        summary = ", ".join(failures)
    else:
        summary = str(exc)
    suffix = (
        "Return one JSON object with only valid fields."
        if strategy == "json_output"
        else "Call the structured output tool once with only valid parameters."
    )
    return (
        "Previous output failed schema validation "
        f"({summary}). Match the schema exactly. Do not remove required fields. Remove only fields "
        "reported as extra; for an invalid nested item, correct it from grounded input or remove "
        "the entire optional list item. Never invent values. Do not add wrapper or extra fields. "
        f"{suffix}"
    )[:500]


def _validate_structured_output[OutputT: BaseModel](
    value: dict[str, Any], output_schema: type[OutputT]
) -> OutputT:
    try:
        return output_schema.model_validate(value)
    except ValidationError as outer_error:
        if len(value) == 1 and isinstance(inner := next(iter(value.values())), dict):
            try:
                return output_schema.model_validate(inner)
            except ValidationError:
                pass
        raise outer_error


def _missing_usage() -> ModelUsage:
    return ModelUsage(
        input_tokens=0,
        output_tokens=0,
        cost_microusd=0,
        usage_missing=True,
    )


def _chat_usage(usage: Any) -> ModelUsage | None:
    if usage is None:
        return None
    return ModelUsage(
        input_tokens=int(usage.prompt_tokens),
        output_tokens=int(usage.completion_tokens),
    )
