from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INCIDENTPILOT_API_", extra="forbid")

    host: str = "127.0.0.1"
    port: int = 8200
    database_url: SecretStr | None = None


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INCIDENTPILOT_AUTH_", extra="forbid")

    profile: Literal["development", "oidc"] = "development"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    alert_source_token: SecretStr | None = None


class ActionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INCIDENTPILOT_ACTION_", extra="forbid")

    enabled: bool = False
    mcp_url: str = "http://127.0.0.1:8102/mcp"
    approval_issuer: str = "https://incidentpilot.local"
    approval_audience: str = "action-mcp"
    approval_signing_key: SecretStr | None = None


class TelemetrySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INCIDENTPILOT_TELEMETRY_", extra="forbid")

    mcp_url: str = "http://127.0.0.1:8101/mcp"


class LlmSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INCIDENTPILOT_LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    provider: Literal["openai", "deepseek", "qwen", "openai-compatible"] = "deepseek"
    base_url: str = "https://api.deepseek.com"
    local_base_url: str = "http://127.0.0.1:11434/v1"
    api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    qwen_api_key: SecretStr | None = None

    @property
    def selected_api_key(self) -> SecretStr | None:
        selected = self.api_key
        if selected is None:
            selected = {
                "deepseek": self.deepseek_api_key,
                "qwen": self.qwen_api_key,
            }.get(self.provider)
        if selected is None or not selected.get_secret_value().strip():
            return None
        return selected


class ModelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INCIDENTPILOT_MODEL_", extra="forbid")

    strong: str | None = None
    fast: str | None = None
    local: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INCIDENTPILOT_",
        extra="forbid",
        populate_by_name=True,
    )

    environment: Literal["development", "test", "production"] = Field(
        default="development",
        validation_alias="INCIDENTPILOT_ENV",
    )
    api: ApiSettings = ApiSettings()
    auth: AuthSettings = AuthSettings()
    actions: ActionSettings = ActionSettings()
    telemetry: TelemetrySettings = TelemetrySettings()
    llm: LlmSettings = LlmSettings()
    models: ModelSettings = ModelSettings()
