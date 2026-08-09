from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast, get_args

import yaml
from pydantic import Field, model_validator

from incidentpilot.domain import DomainModel
from incidentpilot.mcp_servers.common.envelope import ToolEnvelope
from incidentpilot.orchestration.state import RcaDiagnosisDraft
from incidentpilot.telemetry.schemas import ServiceName, TraceFailureType

TAXONOMY_POLICY_VERSION = "taxonomy-v8"
TaxonomySplit = Literal["train", "validation"]
RootCauseCategory = Literal[
    "application_failure",
    "dependency_failure",
    "dependency_unreachable",
    "upstream_rate_limit",
    "cache_failure",
]
_UNREACHABLE_FAILURES = frozenset(
    {
        "name_resolution_error",
        "connection_refused",
        "deadline_exceeded",
        "unavailable",
    }
)
_TRACE_FAILURE_TYPES = frozenset(get_args(TraceFailureType))


class TaxonomySuiteError(ValueError):
    pass


class TaxonomyRca(DomainModel):
    symptom_service: ServiceName
    root_cause_service: ServiceName
    dependency_service: ServiceName | None = None


class TaxonomyFacts(DomainModel):
    cache_hit_success: bool = False
    cache_miss_failure: bool = False
    rate_limit_observed: bool = False
    root_typed_failure_observed: bool = False
    failure_types: list[TraceFailureType] = Field(
        default_factory=lambda: list[TraceFailureType](),
        max_length=12,
    )
    observed_containers: list[ServiceName] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def values_are_unique(self) -> TaxonomyFacts:
        if len(self.failure_types) != len(set(self.failure_types)):
            raise ValueError("failure_types must be unique")
        if len(self.observed_containers) != len(set(self.observed_containers)):
            raise ValueError("observed_containers must be unique")
        return self


class TaxonomyCase(DomainModel):
    id: str = Field(pattern=r"^tax-(?:train|validation)-[a-z0-9-]+$")
    rca: TaxonomyRca
    facts: TaxonomyFacts
    expected_category: RootCauseCategory


class TaxonomySuite(DomainModel):
    schema_version: Literal[1]
    split: TaxonomySplit
    cases: list[TaxonomyCase] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> TaxonomySuite:
        if len(self.cases) != len({case.id for case in self.cases}):
            raise ValueError("taxonomy case ids must be unique")
        expected_prefix = f"tax-{self.split}-"
        if any(not case.id.startswith(expected_prefix) for case in self.cases):
            raise ValueError("taxonomy case id does not match split")
        return self


class TaxonomySuiteResult(DomainModel):
    total: int = Field(ge=1)
    correct: int = Field(ge=0)
    accuracy: float = Field(ge=0, le=1)
    failed_case_ids: list[str]


def classify_taxonomy(
    rca: RcaDiagnosisDraft | TaxonomyRca,
    facts: TaxonomyFacts,
) -> RootCauseCategory:
    if facts.cache_hit_success and facts.cache_miss_failure:
        return "cache_failure"
    if facts.rate_limit_observed:
        return "upstream_rate_limit"
    if "storage_connection_failure" in facts.failure_types:
        return "application_failure"
    if rca.dependency_service and _UNREACHABLE_FAILURES.intersection(facts.failure_types):
        return "dependency_unreachable"
    if facts.root_typed_failure_observed:
        return "application_failure"
    if rca.dependency_service:
        return "dependency_failure"
    return "application_failure"


def extract_taxonomy_facts(
    envelopes: Mapping[str, ToolEnvelope],
    *,
    service: str | None = None,
) -> TaxonomyFacts:
    hit_success = False
    miss_failure = False
    rate_limited = False
    root_typed_failure_observed = False
    failure_types: set[TraceFailureType] = set()
    observed_containers: set[str] = set()

    metric_data = _mapping(envelopes.get("metrics"))
    snapshots = metric_data.get("snapshots")
    if isinstance(snapshots, dict):
        for container_service, raw_metrics in cast(dict[Any, Any], snapshots).items():
            if not isinstance(container_service, str) or not isinstance(raw_metrics, dict):
                continue
            raw_memory = cast(dict[Any, Any], raw_metrics).get("container_memory_usage")
            if not isinstance(raw_memory, dict):
                continue
            value = cast(dict[Any, Any], raw_memory).get("value")
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
            ):
                observed_containers.add(container_service)

    trace_data = _mapping(envelopes.get("traces"))
    raw_traces = trace_data.get("traces")
    if isinstance(raw_traces, list):
        for raw_trace in cast(list[Any], raw_traces):
            if not isinstance(raw_trace, dict):
                continue
            trace = cast(dict[Any, Any], raw_trace)
            error = trace.get("error") is True
            cache_path_values: dict[str, set[bool]] = {}
            for raw_observation in _list(trace.get("observations")):
                if service is not None and raw_observation.get("service") != service:
                    continue
                attributes = raw_observation.get("attributes")
                if not isinstance(attributes, dict):
                    continue
                cache_hit = cast(dict[Any, Any], attributes).get("app.cache_hit")
                hit_success |= cache_hit is True and not error
                miss_failure |= cache_hit is False and error
                if isinstance(cache_hit, bool) and isinstance(
                    observation_service := raw_observation.get("service"), str
                ):
                    cache_path_values.setdefault(observation_service, set()).add(cache_hit)
                rate_limited |= cast(dict[Any, Any], attributes).get("app.rate_limited") is True
            for raw_span in _list(trace.get("error_spans")):
                if service is not None and raw_span.get("service") != service:
                    continue
                failure_type = raw_span.get("failure_type")
                if failure_type == "rate_limited":
                    rate_limited = True
                elif failure_type in _TRACE_FAILURE_TYPES:
                    failure_types.add(cast(TraceFailureType, failure_type))
                    if service is not None:
                        root_typed_failure_observed = True
            if error:
                error_services = {
                    raw_span["service"]
                    for raw_span in _list(trace.get("error_spans"))
                    if isinstance(raw_span.get("service"), str)
                }
                mixed_cache_error = any(
                    values == {False, True} and observation_service in error_services
                    for observation_service, values in cache_path_values.items()
                )
                hit_success |= mixed_cache_error
                miss_failure |= mixed_cache_error

    return TaxonomyFacts(
        cache_hit_success=hit_success,
        cache_miss_failure=miss_failure,
        rate_limit_observed=rate_limited,
        root_typed_failure_observed=root_typed_failure_observed,
        failure_types=sorted(failure_types),
        observed_containers=sorted(observed_containers),
    )


def load_taxonomy_suite(path: Path, *, expected_split: TaxonomySplit) -> list[TaxonomyCase]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TaxonomySuiteError(f"{path.name}: suite must be an object")
    try:
        suite = TaxonomySuite.model_validate(raw)
    except ValueError as exc:
        raise TaxonomySuiteError(f"{path.name}: {exc}") from exc
    if suite.split != expected_split:
        raise TaxonomySuiteError(f"{path.name}: expected split {expected_split}, got {suite.split}")
    return suite.cases


def evaluate_taxonomy_suite(cases: list[TaxonomyCase]) -> TaxonomySuiteResult:
    failures = [
        case.id
        for case in cases
        if classify_taxonomy(case.rca, case.facts) != case.expected_category
    ]
    correct = len(cases) - len(failures)
    return TaxonomySuiteResult(
        total=len(cases),
        correct=correct,
        accuracy=correct / len(cases),
        failed_case_ids=failures,
    )


def verify_taxonomy_manifest(manifest_path: Path, project_root: Path) -> str:
    raw: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TaxonomySuiteError("taxonomy manifest must be an object")
    manifest = cast(dict[str, Any], raw)
    if set(manifest) != {"schema_version", "policy_version", "train", "validation"}:
        raise TaxonomySuiteError("taxonomy manifest has unexpected fields")
    if manifest.get("schema_version") != 1:
        raise TaxonomySuiteError("taxonomy manifest schema version is unsupported")
    policy_version = manifest.get("policy_version")
    if policy_version != TAXONOMY_POLICY_VERSION:
        raise TaxonomySuiteError("taxonomy policy version does not match code")
    for split in ("train", "validation"):
        raw_entry = manifest.get(split)
        if not isinstance(raw_entry, dict):
            raise TaxonomySuiteError(f"taxonomy manifest {split} entry is invalid")
        entry = cast(dict[str, Any], raw_entry)
        if set(entry) != {"path", "sha256"}:
            raise TaxonomySuiteError(f"taxonomy manifest {split} entry is invalid")
        relative = entry.get("path")
        expected_digest = entry.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected_digest, str):
            raise TaxonomySuiteError(f"taxonomy manifest {split} entry is invalid")
        path = (project_root / relative).resolve()
        try:
            path.relative_to(project_root.resolve())
        except ValueError as exc:
            raise TaxonomySuiteError("taxonomy manifest path escapes project root") from exc
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_digest:
            raise TaxonomySuiteError(f"taxonomy manifest digest mismatch: {relative}")
    return cast(str, policy_version)


def _mapping(envelope: ToolEnvelope | None) -> dict[str, Any]:
    if envelope is None or not envelope.ok or not isinstance(envelope.data, dict):
        return {}
    return envelope.data


def _list(value: Any) -> list[dict[Any, Any]]:
    if not isinstance(value, list):
        return []
    return [cast(dict[Any, Any], item) for item in cast(list[Any], value) if isinstance(item, dict)]
