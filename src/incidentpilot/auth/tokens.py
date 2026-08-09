from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
from mcp.server.auth.provider import AccessToken
from pydantic import Field

from incidentpilot.domain import DomainModel

TELEMETRY_SCOPES = frozenset(
    {
        "telemetry:metrics.read",
        "telemetry:logs.read",
        "telemetry:traces.read",
        "telemetry:runbooks.read",
        "telemetry:changes.read",
    }
)
MAX_TELEMETRY_TOKEN_LIFETIME = timedelta(minutes=10)
ACTION_READ_SCOPE = "actions:catalog.read"
ACTION_WRITE_SCOPES = frozenset({"actions:restart", "actions:rollback-change"})
ACTION_SCOPES = ACTION_WRITE_SCOPES
MAX_APPROVAL_GRANT_LIFETIME = timedelta(minutes=5)
MAX_ACTION_CATALOG_TOKEN_LIFETIME = timedelta(minutes=5)


class ApprovalGrant(DomainModel):
    tenant_id: str
    incident_id: str
    proposal_id: str
    proposal_payload_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    actor_id: str
    scope: str
    nonce: str
    issued_at: datetime
    expires_at: datetime


class DevelopmentTelemetryTokenProvider:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        private_key: str,
        clock: Callable[[], datetime] | None = None,
        jti_factory: Callable[[], str] | None = None,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._private_key = private_key
        self._clock = clock or (lambda: datetime.now(UTC))
        self._jti_factory = jti_factory or (lambda: uuid4().hex)

    def mint_telemetry_token(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        scopes: set[str],
        lifetime: timedelta = MAX_TELEMETRY_TOKEN_LIFETIME,
    ) -> str:
        if not tenant_id or not incident_id:
            raise ValueError("tenant_id and incident_id are required")
        if lifetime <= timedelta(0) or lifetime > MAX_TELEMETRY_TOKEN_LIFETIME:
            raise ValueError("telemetry token lifetime must be at most 10 minutes")
        if not scopes or not scopes <= TELEMETRY_SCOPES:
            raise ValueError("telemetry token scopes must be fixed read-only scopes")
        now = self._clock().astimezone(UTC)
        issued_at = int(now.timestamp())
        claims = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": "incidentpilot-worker",
            "tenant_id": tenant_id,
            "incident_id": incident_id,
            "scope": " ".join(sorted(scopes)),
            "iat": issued_at,
            "nbf": issued_at,
            "exp": int((now + lifetime).timestamp()),
            "jti": self._jti_factory(),
        }
        return jwt.encode(claims, self._private_key, algorithm="EdDSA")


class DevelopmentTelemetryTokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        public_key: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._public_key = public_key
        self._clock = clock or (lambda: datetime.now(UTC))

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=["EdDSA"],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": [
                        "iss",
                        "aud",
                        "sub",
                        "tenant_id",
                        "incident_id",
                        "scope",
                        "iat",
                        "nbf",
                        "exp",
                        "jti",
                    ],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
            scopes = self._validate_claims(claims)
        except (jwt.PyJWTError, TypeError, ValueError):
            return None
        return AccessToken(
            token=token,
            client_id=str(claims["sub"]),
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self._audience,
            subject=str(claims["sub"]),
            claims=claims,
        )

    def _validate_claims(self, claims: dict[str, Any]) -> list[str]:
        now = int(self._clock().astimezone(UTC).timestamp())
        issued_at = _integer_claim(claims, "iat")
        not_before = _integer_claim(claims, "nbf")
        expires_at = _integer_claim(claims, "exp")
        if not_before > now or issued_at > now or expires_at <= now:
            raise ValueError("telemetry token is not currently valid")
        if expires_at - issued_at > int(MAX_TELEMETRY_TOKEN_LIFETIME.total_seconds()):
            raise ValueError("telemetry token lifetime exceeds 10 minutes")
        tenant_id = claims.get("tenant_id")
        incident_id = claims.get("incident_id")
        jti = claims.get("jti")
        if not all(isinstance(value, str) and value for value in (tenant_id, incident_id, jti)):
            raise ValueError("telemetry token resource claims are invalid")
        scope_claim = claims.get("scope")
        if not isinstance(scope_claim, str):
            raise ValueError("telemetry token scope claim is invalid")
        scopes = scope_claim.split()
        if not scopes or len(scopes) != len(set(scopes)) or not set(scopes) <= TELEMETRY_SCOPES:
            raise ValueError("telemetry token scopes are invalid")
        return sorted(scopes)


class DevelopmentApprovalGrantProvider:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        private_key: str,
        clock: Callable[[], datetime] | None = None,
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._private_key = private_key
        self._clock = clock or (lambda: datetime.now(UTC))
        self._nonce_factory = nonce_factory or (lambda: uuid4().hex)

    def mint_approval_grant(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        proposal_id: str,
        proposal_payload_digest: str,
        actor_id: str,
        scope: str,
        lifetime: timedelta = MAX_APPROVAL_GRANT_LIFETIME,
    ) -> str:
        if not all((tenant_id, incident_id, proposal_id, actor_id)):
            raise ValueError("approval grant resource claims are required")
        if scope not in ACTION_SCOPES:
            raise ValueError("approval grant requires one fixed action scope")
        if len(proposal_payload_digest) != 64 or any(
            char not in "0123456789abcdef" for char in proposal_payload_digest
        ):
            raise ValueError("proposal payload digest must be sha256")
        if lifetime <= timedelta(0) or lifetime > MAX_APPROVAL_GRANT_LIFETIME:
            raise ValueError("approval grant lifetime must be at most 5 minutes")
        now = self._clock().astimezone(UTC)
        issued_at = int(now.timestamp())
        return jwt.encode(
            {
                "iss": self._issuer,
                "aud": self._audience,
                "sub": "incidentpilot-api",
                "tenant_id": tenant_id,
                "incident_id": incident_id,
                "proposal_id": proposal_id,
                "proposal_payload_digest": proposal_payload_digest,
                "actor_id": actor_id,
                "scope": scope,
                "nonce": self._nonce_factory(),
                "iat": issued_at,
                "nbf": issued_at,
                "exp": int((now + lifetime).timestamp()),
            },
            self._private_key,
            algorithm="EdDSA",
        )


class DevelopmentActionCatalogTokenProvider:
    """Worker-owned read-only credential for Action MCP planning catalog access."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        private_key: str,
        clock: Callable[[], datetime] | None = None,
        jti_factory: Callable[[], str] | None = None,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._private_key = private_key
        self._clock = clock or (lambda: datetime.now(UTC))
        self._jti_factory = jti_factory or (lambda: uuid4().hex)

    def mint_catalog_token(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        lifetime: timedelta = MAX_ACTION_CATALOG_TOKEN_LIFETIME,
    ) -> str:
        if not tenant_id or not incident_id:
            raise ValueError("catalog token resource claims are required")
        if lifetime <= timedelta(0) or lifetime > MAX_ACTION_CATALOG_TOKEN_LIFETIME:
            raise ValueError("catalog token lifetime must be at most 5 minutes")
        now = self._clock().astimezone(UTC)
        issued_at = int(now.timestamp())
        return jwt.encode(
            {
                "iss": self._issuer,
                "aud": self._audience,
                "sub": "incidentpilot-worker",
                "tenant_id": tenant_id,
                "incident_id": incident_id,
                "scope": ACTION_READ_SCOPE,
                "iat": issued_at,
                "nbf": issued_at,
                "exp": int((now + lifetime).timestamp()),
                "jti": self._jti_factory(),
            },
            self._private_key,
            algorithm="EdDSA",
        )


class DevelopmentApprovalGrantVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        public_key: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._public_key = public_key
        self._clock = clock or (lambda: datetime.now(UTC))

    def verify_grant(self, token: str) -> ApprovalGrant | None:
        try:
            raw_claims = jwt.decode(
                token,
                self._public_key,
                algorithms=["EdDSA"],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": [
                        "iss",
                        "aud",
                        "sub",
                        "tenant_id",
                        "incident_id",
                        "proposal_id",
                        "proposal_payload_digest",
                        "actor_id",
                        "scope",
                        "nonce",
                        "iat",
                        "nbf",
                        "exp",
                    ],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
            claims = raw_claims
            now = int(self._clock().astimezone(UTC).timestamp())
            issued_at = _integer_claim(claims, "iat")
            not_before = _integer_claim(claims, "nbf")
            expires_at = _integer_claim(claims, "exp")
            if not_before > now or issued_at > now or expires_at <= now:
                return None
            if expires_at - issued_at > int(MAX_APPROVAL_GRANT_LIFETIME.total_seconds()):
                return None
            if claims.get("sub") != "incidentpilot-api" or claims.get("scope") not in ACTION_SCOPES:
                return None
            values = ("tenant_id", "incident_id", "proposal_id", "actor_id", "nonce")
            if not all(isinstance(claims.get(name), str) and claims[name] for name in values):
                return None
            return ApprovalGrant(
                tenant_id=str(claims["tenant_id"]),
                incident_id=str(claims["incident_id"]),
                proposal_id=str(claims["proposal_id"]),
                proposal_payload_digest=str(claims["proposal_payload_digest"]),
                actor_id=str(claims["actor_id"]),
                scope=str(claims["scope"]),
                nonce=str(claims["nonce"]),
                issued_at=datetime.fromtimestamp(issued_at, tz=UTC),
                expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
            )
        except (jwt.PyJWTError, TypeError, ValueError):
            return None


class DevelopmentActionCatalogTokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        public_key: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._public_key = public_key
        self._clock = clock or (lambda: datetime.now(UTC))

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=["EdDSA"],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": [
                        "iss",
                        "aud",
                        "sub",
                        "tenant_id",
                        "incident_id",
                        "scope",
                        "iat",
                        "nbf",
                        "exp",
                        "jti",
                    ],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
            now = int(self._clock().astimezone(UTC).timestamp())
            issued_at = _integer_claim(claims, "iat")
            not_before = _integer_claim(claims, "nbf")
            expires_at = _integer_claim(claims, "exp")
            if not_before > now or issued_at > now or expires_at <= now:
                return None
            if expires_at - issued_at > int(MAX_ACTION_CATALOG_TOKEN_LIFETIME.total_seconds()):
                return None
            if (
                claims.get("sub") != "incidentpilot-worker"
                or claims.get("scope") != ACTION_READ_SCOPE
            ):
                return None
            if not all(
                isinstance(claims.get(name), str) and claims[name]
                for name in ("tenant_id", "incident_id", "jti")
            ):
                return None
        except (jwt.PyJWTError, TypeError, ValueError):
            return None
        return AccessToken(
            token=token,
            client_id="incidentpilot-worker",
            scopes=[ACTION_READ_SCOPE],
            expires_at=expires_at,
            resource=self._audience,
            subject="incidentpilot-worker",
            claims=claims,
        )


class DevelopmentApprovalGrantTokenVerifier:
    """Expose a verified one-action approval grant to the Action MCP transport."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        public_key: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._verifier = DevelopmentApprovalGrantVerifier(
            issuer=issuer,
            audience=audience,
            public_key=public_key,
            clock=clock,
        )
        self._audience = audience

    async def verify_token(self, token: str) -> AccessToken | None:
        grant = self._verifier.verify_grant(token)
        if grant is None:
            return None
        claims = grant.model_dump(mode="json")
        return AccessToken(
            token=token,
            client_id="incidentpilot-api",
            scopes=[grant.scope],
            expires_at=int(grant.expires_at.timestamp()),
            resource=self._audience,
            subject="incidentpilot-api",
            claims=claims,
        )


def _integer_claim(claims: dict[str, Any], name: str) -> int:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value
