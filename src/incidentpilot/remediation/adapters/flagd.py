from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from incidentpilot.evaluation.isolation import FlagdScenarioController, FlagdSnapshot


class FlagdRollbackError(RuntimeError):
    """Raised when a change rollback cannot safely complete."""


class FlagdRollbackConflictError(FlagdRollbackError):
    """Raised when another configuration writer changes flagd during rollback."""


class UnknownChangeError(FlagdRollbackError):
    """Raised when no server-side mapping exists for a public change ID."""


class ChangeTargetDeniedError(FlagdRollbackError):
    """Raised when the persisted mapping does not own the requested target."""


class FlagdChangeMapping(BaseModel):
    """Decrypted, server-only rollback material stored behind the private mapping table."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    change_id: str = Field(pattern=r"^chg_[a-zA-Z0-9_]+$")
    target_service: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    flag_name: str = Field(min_length=1, max_length=200)
    restore_config: dict[str, Any]
    restore_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class FlagdChangeMappingStore(Protocol):
    def get(self, change_id: str) -> FlagdChangeMapping | None: ...


class InMemoryFlagdChangeMappingStore:
    """Test double for the Action Controller's private mapping repository."""

    def __init__(self, mappings: Iterable[FlagdChangeMapping]) -> None:
        self._mappings = {mapping.change_id: mapping for mapping in mappings}

    def get(self, change_id: str) -> FlagdChangeMapping | None:
        return self._mappings.get(change_id)


@dataclass(frozen=True)
class FlagdRollbackReceipt:
    target_service: str
    reference: str


class FlagdRollbackAdapter:
    """Restore a private flagd snapshot with an optimistic double-read guard."""

    def __init__(
        self,
        *,
        controller: FlagdScenarioController,
        mappings: FlagdChangeMappingStore,
    ) -> None:
        self._controller = controller
        self._mappings = mappings

    def rollback(self, *, change_id: str, target_service: str) -> FlagdRollbackReceipt:
        mapping = self._mappings.get(change_id)
        if mapping is None:
            raise UnknownChangeError("private change mapping was not found")
        return self.rollback_mapping(mapping=mapping, target_service=target_service)

    def rollback_mapping(
        self, *, mapping: FlagdChangeMapping, target_service: str
    ) -> FlagdRollbackReceipt:
        """Rollback an Action Controller-provided mapping without exposing it to callers."""
        if mapping.target_service != target_service:
            raise ChangeTargetDeniedError("change does not belong to the requested target")
        if self._controller.digest(mapping.restore_config) != mapping.restore_digest:
            raise FlagdRollbackError("private change mapping has an invalid snapshot digest")

        action_before = self._controller.snapshot()
        desired = self._restore_target_flag(action_before.config, mapping)
        desired_digest = self._controller.digest(desired)
        if action_before.digest == desired_digest:
            return self._receipt(target_service, mapping.restore_digest)
        latest = self._controller.snapshot()
        if latest.digest != action_before.digest:
            raise FlagdRollbackConflictError("flagd configuration changed before rollback write")

        try:
            self._controller.write_config(desired)
        except Exception as exc:
            self._compensate_ambiguous_write(
                action_before=action_before,
                desired_digest=desired_digest,
            )
            raise FlagdRollbackError("flagd rollback write failed") from exc

        observed = self._controller.snapshot()
        if observed.digest != desired_digest:
            raise FlagdRollbackConflictError("flagd configuration changed during rollback")
        return self._receipt(target_service, mapping.restore_digest)

    @staticmethod
    def _restore_target_flag(
        current_config: Mapping[str, Any], mapping: FlagdChangeMapping
    ) -> dict[str, Any]:
        current = deepcopy(dict(current_config))
        raw_current_flags = current.get("flags")
        raw_restore_flags = mapping.restore_config.get("flags")
        if not isinstance(raw_current_flags, dict) or not isinstance(raw_restore_flags, dict):
            raise FlagdRollbackError("flagd configuration has no flags object")
        current_flags = cast(dict[str, Any], raw_current_flags)
        restore_flags = cast(dict[str, Any], raw_restore_flags)
        restore_flag = restore_flags.get(mapping.flag_name)
        if not isinstance(restore_flag, dict) or mapping.flag_name not in current_flags:
            raise FlagdRollbackError("private change mapping does not match current flagd config")
        current_flags[mapping.flag_name] = deepcopy(cast(dict[str, Any], restore_flag))
        return current

    def _compensate_ambiguous_write(
        self,
        *,
        action_before: FlagdSnapshot,
        desired_digest: str,
    ) -> None:
        observed = self._controller.snapshot()
        if observed.digest != desired_digest:
            if observed.digest != action_before.digest:
                raise FlagdRollbackConflictError(
                    "flagd configuration changed after an ambiguous rollback write"
                )
            return
        try:
            self._controller.write_config(action_before.config)
        except Exception as exc:
            raise FlagdRollbackError("failed to restore action-before flagd snapshot") from exc
        restored = self._controller.snapshot()
        if restored.digest != action_before.digest:
            raise FlagdRollbackError("action-before flagd snapshot was not restored")

    @staticmethod
    def _receipt(target_service: str, restore_digest: str) -> FlagdRollbackReceipt:
        return FlagdRollbackReceipt(
            target_service=target_service,
            reference=f"flagd:rollback:{restore_digest}",
        )
