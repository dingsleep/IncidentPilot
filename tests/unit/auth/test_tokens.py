from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from incidentpilot.auth.tokens import (
    DevelopmentTelemetryTokenProvider,
    DevelopmentTelemetryTokenVerifier,
)
from scripts.mint_dev_token import mint_token

ISSUER = "https://incidentpilot.local"
AUDIENCE = "telemetry-mcp"
NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _keys() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            Encoding.PEM,
            PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


@pytest.mark.asyncio
async def test_development_telemetry_token_is_incident_scoped_and_verifiable() -> None:
    private_key, public_key = _keys()
    provider = DevelopmentTelemetryTokenProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        private_key=private_key,
        clock=lambda: NOW,
        jti_factory=lambda: "jti-1",
    )
    token = provider.mint_telemetry_token(
        tenant_id="local",
        incident_id="inc-1",
        scopes={"telemetry:metrics.read", "telemetry:traces.read"},
        lifetime=timedelta(minutes=5),
    )

    verifier = DevelopmentTelemetryTokenVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        public_key=public_key,
        clock=lambda: NOW,
    )
    access = await verifier.verify_token(token)

    assert access is not None
    assert access.scopes == ["telemetry:metrics.read", "telemetry:traces.read"]
    assert access.claims is not None
    assert access.claims["tenant_id"] == "local"
    assert access.claims["incident_id"] == "inc-1"
    assert access.claims["jti"] == "jti-1"
    assert access.claims["aud"] == AUDIENCE


def test_provider_rejects_long_lived_or_action_scoped_tokens() -> None:
    private_key, _ = _keys()
    provider = DevelopmentTelemetryTokenProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        private_key=private_key,
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError, match="10 minutes"):
        provider.mint_telemetry_token(
            tenant_id="local",
            incident_id="inc-1",
            scopes={"telemetry:metrics.read"},
            lifetime=timedelta(minutes=11),
        )
    with pytest.raises(ValueError, match="read-only"):
        provider.mint_telemetry_token(
            tenant_id="local",
            incident_id="inc-1",
            scopes={"actions:restart"},
        )


@pytest.mark.asyncio
async def test_verifier_rejects_wrong_audience_and_excessive_lifetime() -> None:
    private_key, public_key = _keys()
    verifier = DevelopmentTelemetryTokenVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        public_key=public_key,
        clock=lambda: NOW,
    )
    base_claims = {
        "iss": ISSUER,
        "sub": "incidentpilot-worker",
        "tenant_id": "local",
        "incident_id": "inc-1",
        "scope": "telemetry:metrics.read",
        "iat": NOW,
        "nbf": NOW,
        "exp": NOW + timedelta(minutes=5),
        "jti": "jti-2",
    }

    wrong_audience = jwt.encode(
        {**base_claims, "aud": "action-mcp"},
        private_key,
        algorithm="EdDSA",
    )
    long_lived = jwt.encode(
        {
            **base_claims,
            "aud": AUDIENCE,
            "exp": NOW + timedelta(minutes=11),
        },
        private_key,
        algorithm="EdDSA",
    )

    assert await verifier.verify_token(wrong_audience) is None
    assert await verifier.verify_token(long_lived) is None


def test_mint_dev_token_script_rejects_action_audience() -> None:
    private_key, _ = _keys()
    with pytest.raises(ValueError, match="Telemetry audience"):
        mint_token(
            incident_id="inc-1",
            tenant_id="local",
            scopes={"telemetry:metrics.read"},
            audience="action-mcp",
            minutes=5,
            environment={
                "INCIDENTPILOT_TELEMETRY_SIGNING_KEY": private_key,
                "INCIDENTPILOT_TELEMETRY_AUDIENCE": AUDIENCE,
            },
        )
