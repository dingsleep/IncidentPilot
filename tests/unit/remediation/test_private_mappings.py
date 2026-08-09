from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from incidentpilot.remediation.adapters.flagd import FlagdChangeMapping
from incidentpilot.remediation.private_mappings import PrivateMappingCipher


def _mapping() -> FlagdChangeMapping:
    return FlagdChangeMapping(
        change_id="chg_payment_unreachable",
        target_service="checkout",
        flag_name="paymentUnreachable",
        restore_config={"flags": {"paymentUnreachable": {"defaultVariant": "off"}}},
        restore_digest="a" * 64,
    )


def _key() -> str:
    return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode()


def test_private_mapping_cipher_round_trips_only_for_its_change_id() -> None:
    cipher = PrivateMappingCipher.from_base64(_key())
    encrypted = cipher.encrypt(_mapping())

    assert b"paymentUnreachable" not in encrypted
    assert cipher.decrypt(change_id="chg_payment_unreachable", encrypted=encrypted) == _mapping()
    with pytest.raises(ValueError, match="authentication"):
        cipher.decrypt(change_id="chg_other", encrypted=encrypted)


def test_private_mapping_cipher_rejects_invalid_key_material() -> None:
    with pytest.raises(ValueError, match="32-byte"):
        PrivateMappingCipher.from_base64("not-a-valid-key")
