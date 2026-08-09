from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_start_dev_raises_demo_timeout_only_for_unhealthy_services() -> None:
    script = (ROOT / "scripts" / "start_dev.ps1").read_text(encoding="utf-8")
    timeout = "throw 'Timed out waiting for OpenTelemetry Demo containers to become healthy.'"

    assert "if ($unhealthy.Count -gt 0) {" in script
    assert script.rfind("if ($unhealthy.Count -gt 0) {", 0, script.index(timeout)) != -1


def test_start_dev_runs_the_local_action_profile_unless_read_only_is_requested() -> None:
    start = (ROOT / "scripts" / "start_dev.ps1").read_text(encoding="utf-8")
    stop = (ROOT / "scripts" / "stop_dev.ps1").read_text(encoding="utf-8")

    assert "[switch]$ReadOnly" in start
    assert "--profile', 'actions'" in start
    assert "INCIDENTPILOT_ACTION_ENABLED" in start
    assert "--profile core --profile actions stop" in stop
