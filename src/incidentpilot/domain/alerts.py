from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from incidentpilot.domain import DomainModel
from incidentpilot.domain.enums import Severity


class TimeRange(DomainModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def end_must_not_precede_start(self) -> Self:
        if self.end < self.start:
            raise ValueError("time range end must not precede start")
        return self


class AlertPayload(DomainModel):
    external_id: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(max_length=4000)
    severity: Severity
    starts_at: datetime
    service_hint: str | None = Field(default=None, max_length=200)
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
