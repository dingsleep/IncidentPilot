from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self, cast

import yaml
from jsonschema import Draft202012Validator
from pydantic import Field, model_validator

from incidentpilot.domain import DomainModel
from incidentpilot.domain.enums import Severity

ControlType = Literal["fault", "no_fault", "distractor"]
Split = Literal["train", "validation"]
AllowedAction = Literal["restart_service", "rollback_change"]


class ScenarioLoadError(ValueError):
    """Raised when an Episode suite violates its public contract or catalog."""


class EpisodeAlert(DomainModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=4000)
    severity: Severity
    service_hint: str | None = Field(default=None, max_length=100)
    labels: dict[str, str] = Field(default_factory=dict)


class EpisodeBudgets(DomainModel):
    max_duration_seconds: int = Field(ge=60, le=3600)
    max_read_tool_calls: int = Field(ge=1, le=24)
    max_model_tokens: int = Field(ge=1000, le=200_000)


class RuntimeEpisodeInput(DomainModel):
    """The only Episode object allowed to cross into the Agent runtime."""

    alert: EpisodeAlert
    budgets: EpisodeBudgets


class TrafficSpec(DomainModel):
    adapter: Literal["otel-demo-http"]
    operation: Literal[
        "ads", "ai_assistant", "checkout", "cart", "product_detail", "recommendations"
    ]
    requests: int = Field(ge=1, le=60)


class InjectionSpec(DomainModel):
    adapter: Literal["flagd"]
    operation: Literal["enable"]
    service: str
    scenario_key: str
    variant: str
    warmup_seconds: int = Field(ge=0, le=600)


class GroundTruth(DomainModel):
    root_cause_service: str
    dependency_service: str | None = None
    category: str
    required_signal_kinds: list[Literal["metric", "log", "trace"]] = Field(
        min_length=1, max_length=3
    )


class RecoveryCheck(DomainModel):
    template_id: str
    service: str
    comparator: Literal["lt", "lte", "gt", "gte", "between"]
    threshold: float | list[float]


class RecoverySpec(DomainModel):
    observation_seconds: int = Field(ge=30, le=900)
    checks: list[RecoveryCheck] = Field(min_length=1, max_length=8)


class CleanupSpec(DomainModel):
    adapter: Literal["flagd"]
    operation: Literal["restore_snapshot"]


class ExecutionSpec(DomainModel):
    control_type: ControlType
    injections: list[InjectionSpec] = Field(max_length=2)
    traffic: TrafficSpec | None = None
    ground_truth: GroundTruth | None = None
    expected_abstention: bool | None = None
    allowed_actions: list[AllowedAction] = Field(max_length=2)
    recovery: RecoverySpec
    cleanup: list[CleanupSpec] = Field(max_length=2)

    @model_validator(mode="after")
    def enforce_control_shape(self) -> Self:
        if self.control_type == "no_fault":
            if self.injections or self.cleanup or self.ground_truth is not None:
                raise ValueError("no_fault requires no injections, cleanup, or ground_truth")
            if self.expected_abstention is not True:
                raise ValueError("no_fault requires expected_abstention=true")
        elif self.control_type == "fault":
            if len(self.injections) != 1 or not self.cleanup or self.ground_truth is None:
                raise ValueError("fault requires one injection, cleanup, and ground_truth")
        elif len(self.injections) != 2 or not self.cleanup or self.ground_truth is None:
            raise ValueError("distractor requires two injections, cleanup, and ground_truth")
        return self


class PublicHoldoutCase(DomainModel):
    schema_version: Literal[1]
    case_id: str = Field(pattern=r"^case-h[0-9]{3}$")
    alert: EpisodeAlert
    budgets: EpisodeBudgets


class PrivateHoldoutCase(ExecutionSpec):
    schema_version: Literal[1]
    case_id: str = Field(pattern=r"^case-h[0-9]{3}$")
    public_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class LoadedEpisode:
    id: str
    family: str
    split: Split
    public_input: RuntimeEpisodeInput
    execution: ExecutionSpec


def validate_schema_document(document: dict[str, Any], schema_path: Path) -> None:
    raw_schema: Any = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(raw_schema, dict):
        raise ScenarioLoadError(f"{schema_path.name}: schema must be an object")
    schema = cast(dict[str, Any], raw_schema)
    validator: Any = Draft202012Validator(schema)
    errors: list[Any] = sorted(validator.iter_errors(document), key=str)
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "document"
        raise ScenarioLoadError(f"{schema_path.name}: {location}: {error.message}")


def load_episode_suite(root: Path, catalog_path: Path) -> list[LoadedEpisode]:
    schema_path = root / "schema.json"
    services = _catalog_services(catalog_path)
    episodes: list[LoadedEpisode] = []
    ids: set[str] = set()
    family_splits: dict[str, Split] = {}

    for path in sorted((*root.glob("train/*.yaml"), *root.glob("validation/*.yaml"))):
        document = _read_yaml(path)
        validate_schema_document(document, schema_path)
        scenario_id = cast(str, document["id"])
        family = cast(str, document["family"])
        split = cast(Split, document["split"])
        if scenario_id in ids:
            raise ScenarioLoadError(f"duplicate scenario id: {scenario_id}")
        if (previous := family_splits.get(family)) and previous != split:
            raise ScenarioLoadError(f"family {family} crosses split {previous}/{split}")
        ids.add(scenario_id)
        family_splits[family] = split

        alert = EpisodeAlert.model_validate(document["alert"])
        budgets = EpisodeBudgets.model_validate(document["budgets"])
        execution = ExecutionSpec.model_validate(
            {
                key: document.get(key)
                for key in (
                    "control_type",
                    "injections",
                    "traffic",
                    "ground_truth",
                    "expected_abstention",
                    "allowed_actions",
                    "recovery",
                    "cleanup",
                )
                if key in document
            }
        )
        _validate_services(alert, execution, services)
        episodes.append(
            LoadedEpisode(
                id=scenario_id,
                family=family,
                split=split,
                public_input=RuntimeEpisodeInput(alert=alert, budgets=budgets),
                execution=execution,
            )
        )
    return episodes


def load_public_holdout_suite(root: Path, catalog_path: Path) -> list[PublicHoldoutCase]:
    schema_path = root / "holdout-public.schema.json"
    services = _catalog_services(catalog_path)
    cases: list[PublicHoldoutCase] = []
    for path in sorted((root / "holdout").glob("case-h*.public.yaml")):
        document = _read_yaml(path)
        validate_schema_document(document, schema_path)
        case = PublicHoldoutCase.model_validate(document)
        if case.alert.service_hint and case.alert.service_hint not in services:
            raise ScenarioLoadError(f"unknown service: {case.alert.service_hint}")
        cases.append(case)
    if len(cases) != 4 or len({case.case_id for case in cases}) != 4:
        raise ScenarioLoadError("public holdout suite requires four unique cases")
    return cases


def verify_holdout_manifest(manifest_path: Path, project_root: Path) -> dict[str, str]:
    raw_manifest: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw_manifest, dict):
        raise ScenarioLoadError("holdout manifest must be an object")
    manifest = cast(dict[str, Any], raw_manifest)
    public_digests: dict[str, str] = {}
    entries: list[tuple[str, str]] = []

    raw_public: Any = manifest.get("public_cases")
    raw_schemas: Any = manifest.get("schemas")
    if not isinstance(raw_public, list) or not isinstance(raw_schemas, dict):
        raise ScenarioLoadError("holdout manifest is missing public cases or schemas")
    for raw_entry in cast(list[Any], raw_public):
        entry = _manifest_entry(raw_entry)
        entries.append((entry["path"], entry["sha256"]))
        public_digests[entry["case_id"]] = entry["sha256"]
    for raw_entry in cast(dict[str, Any], raw_schemas).values():
        entry = _manifest_entry(raw_entry, require_case_id=False)
        entries.append((entry["path"], entry["sha256"]))
    if len(public_digests) != 4:
        raise ScenarioLoadError("holdout manifest requires four unique public cases")
    for relative_path, expected_digest in entries:
        path = _manifest_path(project_root, relative_path)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_digest:
            raise ScenarioLoadError(f"manifest digest mismatch: {relative_path}")

    status = manifest.get("status")
    bundle_digest = manifest.get("private_bundle_sha256")
    if status == "SKIPPED_MISSING_PRIVATE_SUITE" and bundle_digest is None:
        return public_digests
    if status != "SEALED" or not isinstance(bundle_digest, str):
        raise ScenarioLoadError("holdout manifest has an invalid private bundle state")
    raw_bundle_path = manifest.get("private_bundle_path")
    if not isinstance(raw_bundle_path, str):
        raise ScenarioLoadError("holdout manifest has no private bundle path")
    bundle_path = _manifest_path(project_root, raw_bundle_path)
    if (
        not bundle_path.is_file()
        or hashlib.sha256(bundle_path.read_bytes()).hexdigest() != bundle_digest
    ):
        raise ScenarioLoadError("manifest digest mismatch: private bundle")
    return public_digests


def _read_yaml(path: Path) -> dict[str, Any]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ScenarioLoadError(f"{path}: expected an object")
    return cast(dict[str, Any], raw)


def _manifest_entry(raw: Any, *, require_case_id: bool = True) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ScenarioLoadError("holdout manifest entry must be an object")
    entry = cast(dict[str, Any], raw)
    required: set[str] = {"path", "sha256"}
    if require_case_id:
        required.add("case_id")
    if not required <= entry.keys() or not all(isinstance(entry[key], str) for key in required):
        raise ScenarioLoadError("holdout manifest entry is incomplete")
    digest = cast(str, entry["sha256"])
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ScenarioLoadError("holdout manifest digest is invalid")
    return cast(dict[str, str], entry)


def _manifest_path(project_root: Path, relative_path: str) -> Path:
    root = project_root.resolve()
    path = (root / relative_path).resolve()
    if path == root or root not in path.parents:
        raise ScenarioLoadError("holdout manifest path escapes the project root")
    return path


def _catalog_services(path: Path) -> set[str]:
    catalog = _read_yaml(path)
    raw_services = catalog.get("services")
    if not isinstance(raw_services, list):
        raise ScenarioLoadError("service catalog has no services list")
    services: set[str] = set()
    for raw_service in cast(list[Any], raw_services):
        if not isinstance(raw_service, dict):
            continue
        service = cast(dict[str, Any], raw_service)
        if isinstance(name := service.get("name"), str):
            services.add(name)
    return services


def _validate_services(
    alert: EpisodeAlert, execution: ExecutionSpec, known_services: set[str]
) -> None:
    referenced = [injection.service for injection in execution.injections]
    referenced.extend(check.service for check in execution.recovery.checks)
    if alert.service_hint:
        referenced.append(alert.service_hint)
    if execution.ground_truth:
        referenced.append(execution.ground_truth.root_cause_service)
        if execution.ground_truth.dependency_service:
            referenced.append(execution.ground_truth.dependency_service)
    unknown = sorted(set(referenced) - known_services)
    if unknown:
        raise ScenarioLoadError(f"unknown service: {', '.join(unknown)}")
