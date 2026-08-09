from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from incidentpilot.evaluation.holdout_crypto import (
    HoldoutCryptoError,
    open_holdout_suite,
    seal_holdout_suite,
)

ROOT = Path(__file__).parents[3]


def _private_case(number: int, *, public_digest: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "case_id": f"case-h{number:03d}",
        "public_digest": public_digest or hashlib.sha256(str(number).encode()).hexdigest(),
        "control_type": "fault",
        "injections": [
            {
                "adapter": "flagd",
                "operation": "enable",
                "service": "payment",
                "scenario_key": "paymentFailure",
                "variant": "100%",
                "warmup_seconds": 30,
            }
        ],
        "ground_truth": {
            "root_cause_service": "payment",
            "category": "dependency_failure",
            "required_signal_kinds": ["metric", "trace"],
        },
        "allowed_actions": ["rollback_change"],
        "recovery": {
            "observation_seconds": 30,
            "checks": [
                {
                    "template_id": "service_error_ratio",
                    "service": "checkout",
                    "comparator": "lt",
                    "threshold": 0.02,
                }
            ],
        },
        "cleanup": [{"adapter": "flagd", "operation": "restore_snapshot"}],
    }


def _cases() -> list[dict[str, Any]]:
    return [_private_case(number) for number in range(1, 5)]


def test_aes_gcm_round_trip_keeps_plaintext_in_memory_only() -> None:
    cases = _cases()
    public_digests = {case["case_id"]: case["public_digest"] for case in cases}

    sealed = seal_holdout_suite(cases, passphrase="correct horse", public_digests=public_digests)
    opened = open_holdout_suite(
        sealed.payload,
        passphrase="correct horse",
        expected_bundle_digest=sealed.digest,
        public_digests=public_digests,
    )

    assert [case.case_id for case in opened] == list(public_digests)
    assert b"paymentFailure" not in sealed.payload


def test_wrong_passphrase_tampering_and_manifest_digest_mismatch_are_hard_failures() -> None:
    cases = _cases()
    public_digests = {case["case_id"]: case["public_digest"] for case in cases}
    sealed = seal_holdout_suite(cases, passphrase="correct", public_digests=public_digests)

    with pytest.raises(HoldoutCryptoError, match="authentication"):
        open_holdout_suite(
            sealed.payload,
            passphrase="wrong",
            expected_bundle_digest=sealed.digest,
            public_digests=public_digests,
        )

    tampered = bytearray(sealed.payload)
    tampered[-8] ^= 1
    with pytest.raises(HoldoutCryptoError, match="digest|package"):
        open_holdout_suite(
            bytes(tampered),
            passphrase="correct",
            expected_bundle_digest=sealed.digest,
            public_digests=public_digests,
        )

    with pytest.raises(HoldoutCryptoError, match="digest"):
        open_holdout_suite(
            sealed.payload,
            passphrase="correct",
            expected_bundle_digest="0" * 64,
            public_digests=public_digests,
        )


def test_duplicate_case_missing_cleanup_and_public_digest_mismatch_are_rejected() -> None:
    cases = _cases()
    public_digests = {case["case_id"]: case["public_digest"] for case in cases}

    duplicate = [*cases[:3], cases[0]]
    with pytest.raises(HoldoutCryptoError, match="duplicate"):
        seal_holdout_suite(duplicate, passphrase="secret", public_digests=public_digests)

    missing_cleanup = _cases()
    missing_cleanup[0].pop("cleanup")
    with pytest.raises(HoldoutCryptoError, match="cleanup"):
        seal_holdout_suite(missing_cleanup, passphrase="secret", public_digests=public_digests)

    mismatched = dict(public_digests)
    mismatched["case-h001"] = "f" * 64
    with pytest.raises(HoldoutCryptoError, match="public digest"):
        seal_holdout_suite(cases, passphrase="secret", public_digests=mismatched)


def test_online_process_imports_do_not_load_holdout_crypto() -> None:
    code = (
        "import sys; import incidentpilot.api.main, incidentpilot.worker.main; "
        "assert 'incidentpilot.evaluation.holdout_crypto' not in sys.modules"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and constant test program
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_private_suite_checkpoint_is_explicitly_skipped_when_bundle_is_absent() -> None:
    bundle = ROOT / "artifacts" / "private" / "holdout-v1.json.enc"
    if not bundle.exists():
        pytest.skip("SKIPPED_MISSING_PRIVATE_SUITE")
    manifest = json.loads(
        (ROOT / "scenarios" / "holdout" / "suite-manifest.json").read_text(encoding="utf-8")
    )
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == manifest["private_bundle_sha256"]
