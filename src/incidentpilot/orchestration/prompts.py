from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import Field

from incidentpilot.domain import DomainModel
from incidentpilot.llm.structured_output import ModelInvocation

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_REQUIRED_SECTIONS = frozenset(
    {
        "Responsibilities",
        "Visible Data",
        "Tool Allowlist",
        "Output Schema",
        "Budget",
        "Stop Conditions",
        "Evidence Rules",
        "Untrusted Data Boundary",
    }
)
_PROMPT_FILES = frozenset(
    {
        "triage",
        "metrics_investigator",
        "logs_investigator",
        "traces_investigator",
        "runbook_analyst",
        "incident_commander",
        "remediation_planner",
        "postmortem_reporter",
    }
)
_INVESTIGATORS = frozenset(
    {
        "metrics_investigator",
        "logs_investigator",
        "traces_investigator",
        "runbook_analyst",
    }
)
_ACTION_TOOLS = frozenset({"restart_service", "rollback_change"})


class PromptMetadata(DomainModel):
    agent: str = Field(pattern=r"^[a-z][a-z0-9_]{1,99}$")
    version: str = Field(pattern=r"^v[1-9][0-9]*$")
    tools: list[str] = Field(max_length=20)
    output_schema: str = Field(min_length=1, max_length=100)
    max_input_chars: int = Field(ge=100, le=100_000)
    max_tool_calls: int = Field(ge=0, le=24)


class VersionedPrompt(DomainModel):
    metadata: PromptMetadata
    content: str
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @property
    def version(self) -> str:
        return self.metadata.version


class PromptSet(DomainModel):
    version: str
    prompts: dict[str, VersionedPrompt]


@dataclass(frozen=True)
class AgentSpec:
    prompt: VersionedPrompt
    tools: tuple[object, ...]

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self.prompt.metadata.tools)

    def invocation(
        self,
        *,
        incident_id: str,
        user_prompt: str,
    ) -> ModelInvocation:
        return ModelInvocation(
            incident_id=incident_id,
            agent_name=self.prompt.metadata.agent,
            prompt_version=self.prompt.version,
            system_prompt=self.prompt.content,
            user_prompt=user_prompt,
        )


class AgentFactory:
    def __init__(
        self,
        *,
        prompts: PromptSet,
        available_tools: Mapping[str, object],
    ) -> None:
        self._prompts = prompts
        self._available_tools = dict(available_tools)

    def build(self, agent_name: str) -> AgentSpec:
        try:
            prompt = self._prompts.prompts[agent_name]
        except KeyError as exc:
            raise ValueError(f"unknown agent: {agent_name}") from exc
        required = tuple(prompt.metadata.tools)
        missing = set(required) - self._available_tools.keys()
        if missing:
            raise ValueError(f"missing required tools: {sorted(missing)}")
        if agent_name in _INVESTIGATORS and _ACTION_TOOLS.intersection(required):
            raise ValueError("investigation agents cannot receive action tools")
        return AgentSpec(
            prompt=prompt,
            tools=tuple(self._available_tools[name] for name in required),
        )


def load_prompt_set(directory: Path) -> PromptSet:
    version = directory.name
    paths = sorted(directory.glob("*.md"))
    if {path.stem for path in paths} != set(_PROMPT_FILES):
        raise ValueError("prompt directory must contain exactly the eight required agents")
    prompts: dict[str, VersionedPrompt] = {}
    for path in paths:
        prompt = _load_prompt(path)
        if prompt.metadata.agent != path.stem:
            raise ValueError(f"prompt agent does not match filename: {path}")
        if prompt.version != version:
            raise ValueError(f"prompt version does not match directory: {path}")
        prompts[path.stem] = prompt
    return PromptSet(version=version, prompts=prompts)


def _load_prompt(path: Path) -> VersionedPrompt:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if match is None:
        raise ValueError(f"{path}: missing YAML frontmatter")
    raw: Any = yaml.safe_load(match.group(1))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: frontmatter must be an object")
    metadata = PromptMetadata.model_validate(cast(dict[str, Any], raw))
    if len(metadata.tools) != len(set(metadata.tools)):
        raise ValueError(f"{path}: duplicate tool names")
    content = text[match.end() :].strip()
    sections = set(re.findall(r"(?m)^##\s+(.+?)\s*$", content))
    missing = _REQUIRED_SECTIONS - sections
    if missing:
        raise ValueError(f"{path}: missing prompt sections: {sorted(missing)}")
    return VersionedPrompt(
        metadata=metadata,
        content=content,
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
