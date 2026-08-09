from __future__ import annotations

from typing import Literal

from pydantic import Field

from incidentpilot.domain import DomainModel

ModelProvider = Literal["openai", "deepseek", "qwen", "openai-compatible"]


class ModelProfile(DomainModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    provider: ModelProvider
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(pattern=r"^https?://", max_length=500)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1, le=500_000)
    supports_tools: bool
    supports_native_schema: bool | None


class ModelProfiles(DomainModel):
    strong: ModelProfile
    fast: ModelProfile
    local: ModelProfile


def build_model_profiles(
    *,
    provider: ModelProvider,
    base_url: str,
    strong_model: str,
    fast_model: str,
    local_model: str,
    local_base_url: str,
) -> ModelProfiles:
    native_schema = False if provider in {"deepseek", "qwen"} else None
    return ModelProfiles(
        strong=ModelProfile(
            name="strong",
            provider=provider,
            model=strong_model,
            base_url=base_url,
            temperature=0,
            max_tokens=8_000,
            supports_tools=True,
            supports_native_schema=native_schema,
        ),
        fast=ModelProfile(
            name="fast",
            provider=provider,
            model=fast_model,
            base_url=base_url,
            temperature=0,
            max_tokens=4_000,
            supports_tools=True,
            supports_native_schema=native_schema,
        ),
        local=ModelProfile(
            name="local",
            provider="openai-compatible",
            model=local_model,
            base_url=local_base_url,
            temperature=0,
            max_tokens=4_000,
            supports_tools=True,
            supports_native_schema=None,
        ),
    )
