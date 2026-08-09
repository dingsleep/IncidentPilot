from pathlib import Path

from scripts.configure_local_security import configure_local_security


def test_local_security_config_is_idempotent_and_preserves_model_keys(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("INCIDENTPILOT_LLM_QWEN_API_KEY=keep-me\n", encoding="utf-8")

    first = configure_local_security(target)
    second = configure_local_security(target)
    content = target.read_text(encoding="utf-8")

    assert "INCIDENTPILOT_LLM_QWEN_API_KEY=keep-me" in content
    assert "INCIDENTPILOT_ACTION_ENABLED=true" in content
    assert "INCIDENTPILOT_ACTION_APPROVAL_SIGNING_KEY=" in content
    assert "INCIDENTPILOT_APPROVAL_VERIFYING_KEY=" in content
    assert "INCIDENTPILOT_PRIVATE_MAPPING_ENCRYPTION_KEY=" in content
    assert "INCIDENTPILOT_TELEMETRY_SIGNING_KEY=" in content
    assert first
    assert second == []

