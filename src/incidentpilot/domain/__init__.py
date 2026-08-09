from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator


class DomainModel(BaseModel):
    """Strict base for framework-independent domain payloads."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def require_aware_datetimes(self) -> Self:
        for value in self.__dict__.values():
            if isinstance(value, datetime) and value.utcoffset() is None:
                raise ValueError("datetime values must be timezone-aware")
        return self
