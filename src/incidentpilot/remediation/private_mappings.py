from __future__ import annotations

import base64
import binascii
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from incidentpilot.incidents.models import ChangeEventPrivateMappingRow
from incidentpilot.remediation.adapters.flagd import FlagdChangeMapping
from incidentpilot.runtime.database import Database

_NONCE_BYTES = 12
_AAD_PREFIX = b"incidentpilot-private-change-mapping:v1:"


class PrivateMappingCipher:
    """Encrypt server-only rollback mappings with per-change authenticated data."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("private mapping key must be 32-byte AES-256 material")
        self._cipher = AESGCM(key)

    @classmethod
    def from_base64(cls, value: str) -> PrivateMappingCipher:
        try:
            key = base64.b64decode(value.encode(), altchars=b"-_", validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("private mapping key must be base64-encoded 32-byte material") from exc
        return cls(key)

    def encrypt(self, mapping: FlagdChangeMapping) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        payload = json.dumps(
            mapping.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return nonce + self._cipher.encrypt(nonce, payload, self._aad(mapping.change_id))

    def decrypt(self, *, change_id: str, encrypted: bytes) -> FlagdChangeMapping:
        if len(encrypted) <= _NONCE_BYTES:
            raise ValueError("private mapping ciphertext is invalid")
        nonce, ciphertext = encrypted[:_NONCE_BYTES], encrypted[_NONCE_BYTES:]
        try:
            payload = self._cipher.decrypt(nonce, ciphertext, self._aad(change_id))
        except InvalidTag as exc:
            raise ValueError("private mapping authentication failed") from exc
        mapping = FlagdChangeMapping.model_validate_json(payload)
        if mapping.change_id != change_id:
            raise ValueError("private mapping change identity mismatch")
        return mapping

    @staticmethod
    def _aad(change_id: str) -> bytes:
        return _AAD_PREFIX + change_id.encode("ascii")


class SqlAlchemyPrivateMappingRepository:
    """Persist encrypted rollback mappings for the evaluation and Action MCP roles.

    The repository deliberately has no listing API: the Action MCP can retrieve a
    mapping only for the specific approved change it is about to roll back.
    """

    def __init__(self, *, database: Database, cipher: PrivateMappingCipher) -> None:
        self._database = database
        self._cipher = cipher

    async def store(self, mapping: FlagdChangeMapping) -> None:
        """Create an immutable mapping, or accept an identical retry idempotently."""
        encrypted = self._cipher.encrypt(mapping)
        async with self._database.session_factory() as session, session.begin():
            row = await session.get(ChangeEventPrivateMappingRow, mapping.change_id)
            if row is None:
                session.add(
                    ChangeEventPrivateMappingRow(
                        change_id=mapping.change_id,
                        mapping_encrypted=encrypted,
                        config_digest=mapping.restore_digest,
                    )
                )
                return

            existing = self._cipher.decrypt(
                change_id=mapping.change_id,
                encrypted=bytes(row.mapping_encrypted),
            )
            if existing != mapping or row.config_digest != mapping.restore_digest:
                raise ValueError("private mapping conflicts with an existing change mapping")

    async def get(self, change_id: str) -> FlagdChangeMapping | None:
        async with self._database.session_factory() as session:
            row = await session.get(ChangeEventPrivateMappingRow, change_id)
        if row is None:
            return None

        mapping = self._cipher.decrypt(
            change_id=change_id,
            encrypted=bytes(row.mapping_encrypted),
        )
        if mapping.restore_digest != row.config_digest:
            raise ValueError("private mapping digest integrity check failed")
        return mapping
