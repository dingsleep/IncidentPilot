from __future__ import annotations

import base64
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from incidentpilot.incidents.models import ChangeEventRow
from incidentpilot.remediation.adapters.flagd import FlagdChangeMapping
from incidentpilot.remediation.private_mappings import (
    PrivateMappingCipher,
    SqlAlchemyPrivateMappingRepository,
)
from incidentpilot.runtime.database import Database

MIGRATION_URL = (
    "postgresql+asyncpg://migration_role:migration-local-only@127.0.0.1:5433/incidentpilot"
)
EVALUATION_URL = (
    "postgresql+asyncpg://evaluation_role:evaluation-local-only@127.0.0.1:5433/incidentpilot"
)
ACTION_URL = "postgresql+asyncpg://action_mcp_role:action-local-only@127.0.0.1:5433/incidentpilot"


def _cipher() -> PrivateMappingCipher:
    key = base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode()
    return PrivateMappingCipher.from_base64(key)


@pytest.mark.integration
async def test_evaluation_persists_encrypted_mapping_for_action_role_only() -> None:
    migration_database = Database(MIGRATION_URL)
    evaluation_database = Database(EVALUATION_URL)
    action_database = Database(ACTION_URL)
    change_id = f"chg_mapping_{uuid4().hex}"
    mapping = FlagdChangeMapping(
        change_id=change_id,
        target_service="checkout",
        flag_name="paymentUnreachable",
        restore_config={"flags": {"paymentUnreachable": {"defaultVariant": "off"}}},
        restore_digest="a" * 64,
    )
    try:
        async with migration_database.session_factory() as session, session.begin():
            session.add(
                ChangeEventRow(
                    id=change_id,
                    service="checkout",
                    change_type="configuration",
                    summary="Configuration change applied to checkout",
                    occurred_at=datetime.now(UTC),
                )
            )
        cipher = _cipher()
        await SqlAlchemyPrivateMappingRepository(
            database=evaluation_database,
            cipher=cipher,
        ).store(mapping)

        loaded = await SqlAlchemyPrivateMappingRepository(
            database=action_database,
            cipher=cipher,
        ).get(change_id)

        assert loaded == mapping
    finally:
        await action_database.dispose()
        await evaluation_database.dispose()
        await migration_database.dispose()
