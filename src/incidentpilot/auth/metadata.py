from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl


def telemetry_auth_settings(
    *,
    issuer: str,
    resource_server_url: str,
) -> AuthSettings:
    return AuthSettings(
        issuer_url=AnyHttpUrl(issuer),
        resource_server_url=AnyHttpUrl(resource_server_url),
        required_scopes=[],
    )
