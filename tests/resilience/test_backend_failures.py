from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel

from incidentpilot.llm.gateway import ModelRateLimitError, StructuredOutputGateway
from incidentpilot.llm.profiles import ModelProfile
from incidentpilot.llm.structured_output import ModelInvocation, RawModelResult
from incidentpilot.llm.usage import ModelCallRecord
from incidentpilot.telemetry.backends.http import ReadOnlyJsonClient
from incidentpilot.telemetry.normalization import TelemetryBackendError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("prometheus_503", "UPSTREAM_UNAVAILABLE"),
        ("opensearch_timeout", "UPSTREAM_TIMEOUT"),
        ("jaeger_malformed", "UPSTREAM_UNAVAILABLE"),
        ("mcp_disconnect", "UPSTREAM_UNAVAILABLE"),
    ],
)
async def test_read_only_backends_normalize_failures(failure: str, expected_code: str) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if failure == "prometheus_503":
            return httpx.Response(503)
        if failure == "opensearch_timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if failure == "jaeger_malformed":
            return httpx.Response(200, content=b"{")
        raise httpx.ConnectError("disconnected", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        backend = ReadOnlyJsonClient(
            client=client,
            base_url="http://telemetry.test",
            retry_backoff_seconds=0,
        )
        with pytest.raises(TelemetryBackendError) as exc:
            await backend.request_json("GET", "/read")

    assert exc.value.code == expected_code
    assert attempts == (3 if failure in {"prometheus_503", "mcp_disconnect"} else 1)


@pytest.mark.asyncio
async def test_result_size_limit_is_a_hard_backend_boundary() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 101))
    ) as client:
        backend = ReadOnlyJsonClient(
            client=client, base_url="http://telemetry.test", max_response_bytes=100
        )
        with pytest.raises(TelemetryBackendError, match="size limit") as exc:
            await backend.request_json("GET", "/read")

    assert exc.value.code == "RESULT_TOO_LARGE"


class _Answer(BaseModel):
    value: int


class _RateLimitedTransport:
    async def invoke(
        self,
        profile: ModelProfile,
        invocation: ModelInvocation,
        *,
        output_schema: type[BaseModel],
    ) -> RawModelResult:
        del profile, invocation, output_schema
        raise ModelRateLimitError("429")


class _Recorder:
    def __init__(self) -> None:
        self.records: list[ModelCallRecord] = []

    async def record(self, record: ModelCallRecord) -> None:
        self.records.append(record)


@pytest.mark.asyncio
async def test_llm_rate_limit_exhausts_a_bounded_retry_budget() -> None:
    recorder = _Recorder()
    gateway = StructuredOutputGateway(
        transport=_RateLimitedTransport(), recorder=recorder, retry_backoff_seconds=0
    )
    with pytest.raises(ModelRateLimitError):
        await gateway.invoke(
            profile=ModelProfile(
                name="resilience", provider="openai", model="test", base_url="https://example.test",
                temperature=0, max_tokens=100, supports_tools=True, supports_native_schema=True,
            ),
            invocation=ModelInvocation(
                incident_id="inc-resilience", agent_name="triage", prompt_version="v1",
                system_prompt="Return a typed result.", user_prompt="Investigate.",
            ),
            output_schema=_Answer,
        )

    assert [record.status for record in recorder.records] == ["RATE_LIMITED"] * 3
