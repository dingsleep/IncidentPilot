from __future__ import annotations

import pytest

from incidentpilot.mcp_servers.actions.runtime import required_environment


def test_required_environment_rejects_missing_or_placeholder_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INCIDENTPILOT_TEST_VALUE", raising=False)
    with pytest.raises(RuntimeError, match="INCIDENTPILOT_TEST_VALUE"):
        required_environment("INCIDENTPILOT_TEST_VALUE")

    monkeypatch.setenv("INCIDENTPILOT_TEST_VALUE", "replace-me")
    with pytest.raises(RuntimeError, match="INCIDENTPILOT_TEST_VALUE"):
        required_environment("INCIDENTPILOT_TEST_VALUE")

    monkeypatch.setenv("INCIDENTPILOT_TEST_VALUE", "configured")
    assert required_environment("INCIDENTPILOT_TEST_VALUE") == "configured"
