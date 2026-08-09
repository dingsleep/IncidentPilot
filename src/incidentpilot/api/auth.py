from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict
from pydantic.types import SecretStr

from incidentpilot.api.errors import ApiProblem
from incidentpilot.config import AuthSettings

LOCAL_ACTOR_HEADER = "x-incidentpilot-actor"


class ActorContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: str
    tenant_id: str
    role: Literal["viewer", "operator", "admin"]


class AuthAdapter(Protocol):
    def authenticate(self, headers: Mapping[str, str]) -> ActorContext: ...


class LocalAuthAdapter:
    _actors = {
        f"local-{role}": ActorContext(actor_id=f"local-{role}", tenant_id="local", role=role)
        for role in ("viewer", "operator", "admin")
    }

    def authenticate(self, headers: Mapping[str, str]) -> ActorContext:
        actor_id = headers.get(LOCAL_ACTOR_HEADER)
        if not actor_id:
            raise ApiProblem(
                status=401,
                code="AUTH_REQUIRED",
                title="Unauthorized",
                detail="Local actor header is required.",
            )
        actor = self._actors.get(actor_id)
        if actor is None:
            raise ApiProblem(
                status=403,
                code="ACTOR_FORBIDDEN",
                title="Forbidden",
                detail="Unknown local actor.",
            )
        return actor


class AlertSourceAuthenticator:
    def __init__(self, token: SecretStr | None) -> None:
        self._token = token

    def authenticate(self, headers: Mapping[str, str]) -> None:
        if self._token is None:
            raise ApiProblem(
                status=503,
                code="ALERT_SOURCE_DISABLED",
                title="Service Unavailable",
                detail="Alert ingestion is not configured.",
            )
        authorization = headers.get("authorization", "")
        scheme, _, supplied = authorization.partition(" ")
        if (
            scheme.casefold() != "bearer"
            or not supplied
            or not hmac.compare_digest(
                supplied.encode(),
                self._token.get_secret_value().encode(),
            )
        ):
            raise ApiProblem(
                status=401,
                code="ALERT_SOURCE_UNAUTHORIZED",
                title="Unauthorized",
                detail="Valid alert-source credentials are required.",
            )


def build_auth_adapter(
    *,
    environment: Literal["development", "test", "production"],
    settings: AuthSettings,
    oidc_adapter: AuthAdapter | None = None,
) -> AuthAdapter:
    if settings.profile == "development":
        if environment != "development":
            raise RuntimeError("Local authentication is restricted to the development environment")
        return LocalAuthAdapter()
    if not settings.oidc_issuer or not settings.oidc_audience:
        raise RuntimeError("OIDC issuer and audience are required outside development")
    if oidc_adapter is None:
        raise RuntimeError("A configured OIDC auth adapter is required outside development")
    return oidc_adapter
