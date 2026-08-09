from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, cast

from pydantic import Field

from incidentpilot.domain import DomainModel

TrajectorySplit = Literal["train", "validation", "holdout"]
_SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "signature",
    "private",
    "password",
    "email",
    "phone",
    "address",
    "execution",
    "injection",
    "ground_truth",
    "flag_mapping",
)


class HoldoutTrajectoryError(ValueError):
    pass


class ObservableMessage(DomainModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(max_length=40_000)


class ObservableToolCall(DomainModel):
    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any]
    result: dict[str, Any] | list[Any]


class TrajectoryProvenance(DomainModel):
    run_id: str = Field(min_length=1, max_length=64)
    scenario_id: str = Field(min_length=1, max_length=200)
    seed: int = Field(ge=0)
    split: TrajectorySplit
    source: str = Field(min_length=1, max_length=200)
    license: str = Field(min_length=1, max_length=200)


class TrajectoryQuality(DomainModel):
    hard_failure_free: bool = True
    evidence_consistent: bool = True
    format_valid: bool = True
    tools_complete: bool = True
    environment_clean: bool = True


class ExportedTrajectory(DomainModel):
    provenance: TrajectoryProvenance
    messages: list[ObservableMessage]
    tool_calls: list[ObservableToolCall]
    evidence: list[dict[str, Any]]
    diagnosis: dict[str, Any] | None
    reward_components: dict[str, float]
    model_version: str
    prompt_version: str
    tool_version: str
    quality: TrajectoryQuality
    quality_reasons: list[str]
    content_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


def export_trajectory(
    *,
    provenance: TrajectoryProvenance,
    messages: list[ObservableMessage],
    tool_calls: list[ObservableToolCall],
    evidence: list[dict[str, Any]],
    diagnosis: dict[str, Any] | None,
    reward_components: dict[str, float],
    model_version: str,
    prompt_version: str,
    tool_version: str,
    quality: dict[str, bool] | None = None,
) -> ExportedTrajectory:
    if provenance.split == "holdout":
        raise HoldoutTrajectoryError("holdout trajectories cannot be exported for datasets")
    resolved_quality = TrajectoryQuality.model_validate(quality or {})
    sanitized = {
        "provenance": provenance.model_dump(mode="json"),
        "messages": [message.model_dump(mode="json") for message in messages],
        "tool_calls": [_redact(call.model_dump(mode="json")) for call in tool_calls],
        "evidence": [_redact(item) for item in evidence],
        "diagnosis": _redact(diagnosis),
        "reward_components": reward_components,
        "model_version": model_version,
        "prompt_version": prompt_version,
        "tool_version": tool_version,
        "quality": resolved_quality.model_dump(mode="json"),
        "quality_reasons": quality_reasons(resolved_quality),
    }
    content = {key: value for key, value in sanitized.items() if key != "provenance"}
    return ExportedTrajectory.model_validate(
        {
            **sanitized,
            "content_digest": _digest(content),
            "digest": _digest(sanitized),
        }
    )


def quality_reasons(quality: TrajectoryQuality) -> list[str]:
    checks = (
        (quality.hard_failure_free, "HARD_FAILURE"),
        (quality.evidence_consistent, "EVIDENCE_INCONSISTENT"),
        (quality.format_valid, "FORMAT_REPAIR_FAILED"),
        (quality.tools_complete, "TOOL_RESULT_MISSING"),
        (quality.environment_clean, "ENVIRONMENT_CONTAMINATED"),
    )
    return [reason for passed, reason in checks if not passed]


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        dictionary = cast(dict[object, Any], value)
        return {
            str(key): "[REDACTED]" if _is_sensitive_key(str(key)) else _redact(item)
            for key, item in dictionary.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in cast(list[Any], value)]
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
