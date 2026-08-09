from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast

import httpx

_EPISODE_LOCK = threading.RLock()


class FlagdScenarioError(RuntimeError):
    """Raised when a flagd episode cannot be applied or verified."""


class FlagdRestorationError(FlagdScenarioError):
    """Raised when the original flagd snapshot cannot be restored exactly."""


@dataclass(frozen=True)
class FlagdSnapshot:
    config: dict[str, Any]
    digest: str


class FlagdScenarioController:
    """Apply one flagd variant and always restore the complete original config."""

    def __init__(
        self,
        *,
        client: httpx.Client,
        base_url: str = "http://127.0.0.1:8080/feature/api",
        poll_interval: float = 0.5,
        timeout: float = 15.0,
    ) -> None:
        self._client = client
        self._read_url = f"{base_url.rstrip('/')}/read"
        self._write_url = f"{base_url.rstrip('/')}/write"
        self._poll_interval = poll_interval
        self._timeout = timeout

    @staticmethod
    def digest(config: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            config,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    def read_config(self) -> dict[str, Any]:
        response = self._client.get(self._read_url)
        response.raise_for_status()
        raw_config: Any = response.json()
        if not isinstance(raw_config, dict):
            raise FlagdScenarioError("flagd returned an invalid full configuration")
        config = cast(dict[str, Any], raw_config)
        if not isinstance(config.get("flags"), dict):
            raise FlagdScenarioError("flagd returned an invalid full configuration")
        return deepcopy(config)

    def snapshot(self) -> FlagdSnapshot:
        config = self.read_config()
        return FlagdSnapshot(config=config, digest=self.digest(config))

    @contextmanager
    def activate(self, flag_name: str, variant: str) -> Generator[FlagdSnapshot]:
        with self.activate_many([(flag_name, variant)]) as snapshot:
            yield snapshot

    @contextmanager
    def activate_many(
        self,
        variants: Sequence[tuple[str, str]],
        *,
        snapshot: FlagdSnapshot | None = None,
    ) -> Generator[FlagdSnapshot]:
        with _EPISODE_LOCK:
            original = snapshot or self.snapshot()
            if not variants:
                yield original
                return
            active = original.config
            try:
                for flag_name, variant in variants:
                    active = self._with_variant(active, flag_name, variant)
                    self.write_config(active)
                    self._wait_for_variant(flag_name, variant)
                yield original
            finally:
                try:
                    self.write_config(original.config)
                    self._wait_for_digest(original.digest)
                except Exception as exc:
                    raise FlagdRestorationError(
                        f"failed to restore flagd snapshot {original.digest}"
                    ) from exc

    def _with_variant(
        self, config: Mapping[str, Any], flag_name: str, variant: str
    ) -> dict[str, Any]:
        updated = deepcopy(dict(config))
        raw_flags = updated.get("flags")
        if not isinstance(raw_flags, dict):
            raise FlagdScenarioError("flagd configuration has no flags object")
        flags = cast(dict[str, Any], raw_flags)
        if flag_name not in flags:
            raise FlagdScenarioError(f"unknown flag: {flag_name}")
        raw_flag = flags[flag_name]
        if not isinstance(raw_flag, dict):
            raise FlagdScenarioError(f"invalid flag definition: {flag_name}")
        flag = cast(dict[str, Any], raw_flag)
        raw_variants = flag.get("variants")
        if not isinstance(raw_variants, dict):
            raise FlagdScenarioError(f"invalid flag definition: {flag_name}")
        variants = cast(dict[str, Any], raw_variants)
        if variant not in variants:
            raise FlagdScenarioError(f"unknown variant for {flag_name}: {variant}")
        flag["defaultVariant"] = variant
        return updated

    def write_config(self, config: Mapping[str, Any]) -> None:
        """Write a complete server-produced configuration to flagd."""
        response = self._client.post(self._write_url, json={"data": config})
        response.raise_for_status()

    def _wait_for_variant(self, flag_name: str, variant: str) -> None:
        def applied(config: Mapping[str, Any]) -> bool:
            raw_flags = config.get("flags")
            if not isinstance(raw_flags, dict):
                return False
            flags = cast(dict[str, Any], raw_flags)
            raw_flag = flags.get(flag_name)
            if not isinstance(raw_flag, dict):
                return False
            flag = cast(dict[str, Any], raw_flag)
            return flag.get("defaultVariant") == variant

        self._wait_until(applied, f"flag {flag_name} variant {variant}")

    def _wait_for_digest(self, expected_digest: str) -> None:
        self._wait_until(
            lambda config: self.digest(config) == expected_digest,
            f"snapshot digest {expected_digest}",
        )

    def _wait_until(self, predicate: Callable[[Mapping[str, Any]], bool], description: str) -> None:
        deadline = time.monotonic() + self._timeout
        while True:
            if predicate(self.read_config()):
                return
            if time.monotonic() >= deadline:
                raise FlagdScenarioError(f"timed out waiting for {description}")
            time.sleep(self._poll_interval)


@contextmanager
def episode_environment_lock() -> Generator[None]:
    """Serialize complete Episode windows against the shared Demo environment."""
    with _EPISODE_LOCK:
        yield
