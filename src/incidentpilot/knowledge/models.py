from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunbookMetadata(KnowledgeModel):
    id: str = Field(pattern=r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$", max_length=100)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=100)
    services: list[str] = Field(min_length=1, max_length=20)
    symptoms: list[str] = Field(min_length=1, max_length=30)
    preconditions: list[str] = Field(min_length=1, max_length=30)
    risk: Literal["low", "medium", "high"]
    last_verified_at: date
    sources: list[str] = Field(min_length=1, max_length=20)


class RunbookSection(KnowledgeModel):
    runbook_id: str
    version: str
    section_id: str
    title: str
    parent_title: str | None = None
    content: str
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    runbook_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    services: list[str]
    symptoms: list[str]


class RunbookDocument(RunbookMetadata):
    path: str
    title: str
    content: str
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    sections: list[RunbookSection] = Field(min_length=1)


class RunbookHit(KnowledgeModel):
    runbook_id: str
    version: str
    section_id: str
    title: str
    parent_title: str | None
    snippet: str
    checksum: str
    score: float
