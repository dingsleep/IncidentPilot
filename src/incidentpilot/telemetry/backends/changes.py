from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChangeEvent(BaseModel):
    """Agent-visible, deliberately redacted change metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    change_id: str = Field(pattern=r"^chg_[a-zA-Z0-9_]+$")
    service: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    occurred_at: datetime
    change_type: Literal["configuration"] = "configuration"
    summary: str = Field(min_length=1, max_length=200)

    @field_validator("occurred_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value.astimezone(UTC)


class PrivateChangeMapping(BaseModel):
    """Episode-only mapping that must never enter Agent-visible payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    change_id: str
    scenario_key: str
    flag_name: str
    variant: str
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def create_episode_change(
    *,
    service: str,
    scenario_key: str,
    flag_name: str,
    variant: str,
    snapshot_digest: str,
    change_id: str | None = None,
    occurred_at: datetime | None = None,
) -> tuple[ChangeEvent, PrivateChangeMapping]:
    """Create linked public/private records without accepting a public free-text summary."""
    resolved_change_id = change_id or f"chg_{uuid4().hex}"
    public = ChangeEvent(
        change_id=resolved_change_id,
        service=service,
        occurred_at=occurred_at or datetime.now(UTC),
        summary=f"Configuration change applied to {service}",
    )
    private = PrivateChangeMapping(
        change_id=resolved_change_id,
        scenario_key=scenario_key,
        flag_name=flag_name,
        variant=variant,
        snapshot_digest=snapshot_digest,
    )
    return public, private
