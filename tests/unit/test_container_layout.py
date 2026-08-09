from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_online_images_are_nonroot_and_exclude_evaluation_material() -> None:
    forbidden = ("scenarios", "evaluation", "artifacts/private", ".env")
    for name in ("api", "worker", "mcp"):
        dockerfile = (ROOT / "infra" / "docker" / f"{name}.Dockerfile").read_text()
        assert "USER incidentpilot" in dockerfile
        assert "requirements.runtime.lock" in dockerfile
        assert all(f"COPY {item}" not in dockerfile for item in forbidden)


def test_compose_keeps_writes_out_of_the_core_profile() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    services = compose["services"]
    assert set(services["action-mcp"]["profiles"]) == {"actions"}
    assert "volumes" not in services["action-mcp"]
    assert "env_file" not in services["action-mcp"]
    assert services["action-mcp"]["environment"]["INCIDENTPILOT_ACTION_ENABLED"] == "true"
    assert services["action-mcp"]["environment"]["INCIDENTPILOT_ACTION_FLAGD_API_URL"] == (
        "http://flagd-ui:4000/api"
    )
    assert "incidentpilot.mcp_servers.actions.runtime" in str(services["action-mcp"])
    assert "env_file" not in services["graph-worker"]
    assert "INCIDENTPILOT_ACTION_APPROVAL_SIGNING_KEY" in services["incident-api"]["environment"]
    assert "INCIDENTPILOT_APPROVAL_VERIFYING_KEY" not in services["incident-api"]["environment"]
    assert (
        "INCIDENTPILOT_ACTION_APPROVAL_SIGNING_KEY"
        not in services["graph-worker"]["environment"]
    )
    assert "INCIDENTPILOT_APPROVAL_VERIFYING_KEY" in services["graph-worker"]["environment"]
    assert "INCIDENTPILOT_PRIVATE_MAPPING_ENCRYPTION_KEY" in services["demo-runner"]["environment"]
    assert "opentelemetry-demo" in compose["networks"]
    for name in ("incident-api", "graph-worker", "telemetry-mcp", "incident-web"):
        assert "core" in services[name]["profiles"]
        assert "volumes" not in services[name]
        for port in services[name].get("ports", []):
            assert str(port).startswith("127.0.0.1:")
    assert "${INCIDENTPILOT_API_HOST_PORT:-8200}" in str(services["incident-api"]["ports"])
    assert "${INCIDENTPILOT_WEB_HOST_PORT:-5173}" in str(services["incident-web"]["ports"])


def test_evaluation_runner_is_an_isolated_profile_image() -> None:
    dockerfile = (ROOT / "infra" / "docker" / "evaluation.Dockerfile").read_text()
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    assert "COPY scenarios ./scenarios" in dockerfile
    assert compose["services"]["episode-runner"]["profiles"] == ["evaluation"]


def test_evaluation_build_context_keeps_public_scenarios_but_excludes_artifacts() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()

    assert "scenarios" not in dockerignore
    assert "artifacts" in dockerignore


def test_read_only_web_uses_tmpfs_for_nginx_runtime_directories() -> None:
    nginx = (ROOT / "infra" / "docker" / "nginx.conf").read_text()
    assert "client_body_temp_path /tmp/client_temp;" in nginx
    assert "proxy_temp_path /tmp/proxy_temp;" in nginx


def test_container_processes_use_container_database_and_heartbeat_grants() -> None:
    worker = (ROOT / "scripts" / "run_read_only_worker.py").read_text()
    grants = (ROOT / "infra" / "postgres" / "init-roles.sql").read_text()
    assert 'os.environ.get("INCIDENTPILOT_WORKER_DATABASE_URL"' in worker
    assert "service_heartbeats TO telemetry_mcp_role" in grants
