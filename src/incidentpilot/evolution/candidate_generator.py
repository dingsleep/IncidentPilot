from __future__ import annotations

import hashlib
import json
from difflib import unified_diff
from typing import Literal

from pydantic import ConfigDict, Field

from incidentpilot.domain import DomainModel
from incidentpilot.evolution.failure_mining import FailureCluster

CandidateKind = Literal["prompt", "tool_description", "runbook_draft"]
_SUPPORTED_KINDS: tuple[CandidateKind, ...] = ("prompt", "tool_description", "runbook_draft")


class CandidateArtifact(DomainModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^candidate-[a-f0-9]{12}$")
    kind: CandidateKind
    base_version: str = Field(min_length=1, max_length=100)
    base_content: str
    proposed_content: str
    diff: str
    target_agent: str = Field(pattern=r"^[a-z][a-z0-9_]{1,99}$")
    target_failure_label: str
    target_component: str
    generator_model: str = Field(min_length=1, max_length=100)
    status: Literal["candidate"] = "candidate"
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


def generate_candidates(
    *,
    cluster: FailureCluster,
    base_version: str,
    base_content: str,
    target_agent: str,
    generator_model: str,
) -> list[CandidateArtifact]:
    return [
        generate_candidate(
            kind=kind,
            cluster=cluster,
            base_version=base_version,
            base_content=base_content,
            target_agent=target_agent,
            generator_model=generator_model,
        )
        for kind in _SUPPORTED_KINDS
    ]


def generate_candidate(
    *,
    kind: str,
    cluster: FailureCluster,
    base_version: str,
    base_content: str,
    target_agent: str,
    generator_model: str,
) -> CandidateArtifact:
    if kind not in _SUPPORTED_KINDS:
        raise ValueError(f"unsupported candidate kind: {kind}")
    candidate_kind = kind
    proposed_content = _proposed_content(candidate_kind, base_content, cluster)
    payload = {
        "kind": candidate_kind,
        "base_version": base_version,
        "base_content": base_content,
        "proposed_content": proposed_content,
        "target_agent": target_agent,
        "target_failure_label": cluster.label,
        "target_component": cluster.affected_component,
        "generator_model": generator_model,
    }
    digest = _digest(payload)
    return CandidateArtifact(
        id=f"candidate-{digest[:12]}",
        kind=candidate_kind,
        base_version=base_version,
        base_content=base_content,
        proposed_content=proposed_content,
        diff="\n".join(
            unified_diff(
                base_content.splitlines(),
                proposed_content.splitlines(),
                fromfile=base_version,
                tofile=f"candidate:{candidate_kind}",
                lineterm="",
            )
        ),
        target_agent=target_agent,
        target_failure_label=cluster.label,
        target_component=cluster.affected_component,
        generator_model=generator_model,
        digest=digest,
    )


def _proposed_content(
    kind: CandidateKind,
    base_content: str,
    cluster: FailureCluster,
) -> str:
    label = cluster.label.replace("_", " ")
    if kind == "prompt":
        addition = f"Verify {label} before completing the response."
    elif kind == "tool_description":
        addition = f"Tool guidance: mitigate {label} for {cluster.affected_component}."
    else:
        addition = f"## Candidate-only draft\n\nVerify {label} before recovery."
    return f"{base_content.rstrip()}\n\n{addition}\n"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
