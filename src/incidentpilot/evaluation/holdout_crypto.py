from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from incidentpilot.evaluation.loader import (
    PrivateHoldoutCase,
    ScenarioLoadError,
    validate_schema_document,
)

_AAD = b"incidentpilot-holdout-v1"
_ROOT = Path(__file__).parents[3]
_PRIVATE_SCHEMA = _ROOT / "scenarios" / "holdout-private.schema.json"


class HoldoutCryptoError(ValueError):
    """Raised when a private holdout package is invalid or unauthenticated."""


@dataclass(frozen=True)
class SealedHoldout:
    payload: bytes
    digest: str


def seal_holdout_suite(
    cases: list[dict[str, Any]],
    *,
    passphrase: str,
    public_digests: dict[str, str],
) -> SealedHoldout:
    validated = _validate_cases(cases, public_digests)
    if not passphrase:
        raise HoldoutCryptoError("holdout passphrase must not be empty")
    plaintext = _canonical_json(
        {
            "schema_version": 1,
            "cases": [case.model_dump(mode="json", exclude_none=True) for case in validated],
        }
    )
    salt = os.urandom(16)
    nonce = os.urandom(12)
    ciphertext = AESGCM(_derive_key(passphrase, salt)).encrypt(nonce, plaintext, _AAD)
    payload = _canonical_json(
        {
            "schema_version": 1,
            "kdf": {"name": "scrypt", "n": 16384, "r": 8, "p": 1},
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
    )
    return SealedHoldout(payload=payload, digest=hashlib.sha256(payload).hexdigest())


def open_holdout_suite(
    payload: bytes,
    *,
    passphrase: str,
    expected_bundle_digest: str,
    public_digests: dict[str, str],
) -> list[PrivateHoldoutCase]:
    actual_digest = hashlib.sha256(payload).hexdigest()
    if actual_digest != expected_bundle_digest:
        raise HoldoutCryptoError("encrypted bundle digest mismatch")
    try:
        raw_package: Any = json.loads(payload)
        if not isinstance(raw_package, dict):
            raise ValueError("package must be an object")
        package = cast(dict[str, Any], raw_package)
        if set(package) != {"schema_version", "kdf", "salt", "nonce", "ciphertext"}:
            raise ValueError("unexpected package fields")
        if package["schema_version"] != 1 or package["kdf"] != {
            "name": "scrypt",
            "n": 16384,
            "r": 8,
            "p": 1,
        }:
            raise ValueError("unsupported package version")
        salt = base64.b64decode(package["salt"], validate=True)
        nonce = base64.b64decode(package["nonce"], validate=True)
        ciphertext = base64.b64decode(package["ciphertext"], validate=True)
        plaintext = AESGCM(_derive_key(passphrase, salt)).decrypt(nonce, ciphertext, _AAD)
        raw_document: Any = json.loads(plaintext)
        if not isinstance(raw_document, dict):
            raise ValueError("invalid plaintext document")
        document = cast(dict[str, Any], raw_document)
        if document.get("schema_version") != 1:
            raise ValueError("invalid plaintext document")
        raw_cases: Any = document.get("cases")
        if not isinstance(raw_cases, list) or not all(
            isinstance(case, dict) for case in cast(list[Any], raw_cases)
        ):
            raise ValueError("invalid plaintext cases")
    except InvalidTag as exc:
        raise HoldoutCryptoError("holdout package authentication failed") from exc
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HoldoutCryptoError("invalid encrypted holdout package") from exc
    return _validate_cases(cast(list[dict[str, Any]], raw_cases), public_digests)


def _validate_cases(
    cases: list[dict[str, Any]], public_digests: dict[str, str]
) -> list[PrivateHoldoutCase]:
    case_ids = [str(case.get("case_id", "")) for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise HoldoutCryptoError("duplicate private holdout case")
    if len(cases) != 4:
        raise HoldoutCryptoError("private holdout suite requires exactly four cases")
    validated: list[PrivateHoldoutCase] = []
    try:
        for case in cases:
            validate_schema_document(case, _PRIVATE_SCHEMA)
            validated.append(PrivateHoldoutCase.model_validate(case))
    except (ScenarioLoadError, ValueError) as exc:
        raise HoldoutCryptoError(str(exc)) from exc
    if set(case_ids) != set(public_digests):
        raise HoldoutCryptoError("private/public holdout case set mismatch")
    for case in validated:
        if case.public_digest != public_digests[case.case_id]:
            raise HoldoutCryptoError(f"public digest mismatch for {case.case_id}")
    return sorted(validated, key=lambda case: case.case_id)


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=16384, r=8, p=1).derive(passphrase.encode("utf-8"))


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
