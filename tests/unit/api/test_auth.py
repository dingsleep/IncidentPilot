from __future__ import annotations

import pytest
from pydantic import SecretStr

from incidentpilot.api.auth import ActorContext, LocalAuthAdapter, build_auth_adapter
from incidentpilot.api.errors import ApiProblem
from incidentpilot.api.main import create_app
from incidentpilot.config import ApiSettings, AuthSettings, Settings


def test_local_auth_maps_only_seeded_actors() -> None:
    adapter = LocalAuthAdapter()

    assert adapter.authenticate({"x-incidentpilot-actor": "local-operator"}) == ActorContext(
        actor_id="local-operator",
        tenant_id="local",
        role="operator",
    )

    with pytest.raises(ApiProblem, match="Local actor header is required"):
        adapter.authenticate({})
    with pytest.raises(ApiProblem, match="Unknown local actor"):
        adapter.authenticate({"x-incidentpilot-actor": "attacker"})


def test_local_auth_is_development_only_and_oidc_is_required_elsewhere() -> None:
    with pytest.raises(RuntimeError, match="development environment"):
        build_auth_adapter(
            environment="production",
            settings=AuthSettings(profile="development"),
        )

    with pytest.raises(RuntimeError, match="OIDC issuer and audience"):
        build_auth_adapter(
            environment="production",
            settings=AuthSettings(profile="oidc"),
        )


async def test_api_startup_refuses_non_development_without_oidc_config() -> None:
    app = create_app(
        Settings(
            environment="production",
            api=ApiSettings(
                database_url=SecretStr("postgresql+asyncpg://unused:unused@127.0.0.1/unused")
            ),
            auth=AuthSettings(profile="oidc"),
        )
    )

    with pytest.raises(RuntimeError, match="OIDC issuer and audience"):
        async with app.router.lifespan_context(app):
            pass
