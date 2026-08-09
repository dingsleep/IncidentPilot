from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from incidentpilot import __version__
from incidentpilot.config import LlmSettings, Settings


def test_package_version_and_safe_defaults() -> None:
    assert __version__ == "0.1.0"
    assert Settings().actions.enabled is False
    assert Settings().llm.provider == "deepseek"
    assert Settings().llm.api_key is None


def test_settings_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"unknown_setting": True})


def test_llm_settings_select_provider_key_from_local_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "INCIDENTPILOT_LLM_QWEN_API_KEY=qwen-test-only\n"
        "INCIDENTPILOT_LLM_DEEPSEEK_API_KEY=deepseek-test-only\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    qwen = LlmSettings(provider="qwen")
    deepseek = LlmSettings(provider="deepseek")

    assert qwen.selected_api_key is not None
    assert qwen.selected_api_key.get_secret_value() == "qwen-test-only"
    assert deepseek.selected_api_key is not None
    assert deepseek.selected_api_key.get_secret_value() == "deepseek-test-only"


def test_llm_settings_ignore_empty_provider_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "INCIDENTPILOT_LLM_QWEN_API_KEY=\nINCIDENTPILOT_LLM_DEEPSEEK_API_KEY=\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert LlmSettings(provider="qwen").selected_api_key is None
    assert LlmSettings(provider="deepseek").selected_api_key is None


def test_generic_process_key_overrides_provider_key() -> None:
    settings = LlmSettings(
        provider="qwen",
        api_key=SecretStr("process-test-only"),
        qwen_api_key=SecretStr("file-test-only"),
    )

    assert settings.selected_api_key is not None
    assert settings.selected_api_key.get_secret_value() == "process-test-only"
