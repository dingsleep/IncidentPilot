from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


@pytest.mark.integration
def test_otel_demo_smoke() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/smoke_otel_demo.py"],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_cart_failure_badhost_resolves_inside_demo_network() -> None:
    root = Path(__file__).parents[2]
    docker = shutil.which("docker")
    assert docker is not None
    result = subprocess.run(  # noqa: S603 - fixed compose arguments and resolved executable
        [
            docker,
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            str(root / "infra" / "otel-demo" / "docker-compose.incidentpilot.yml"),
            "config",
            "--format",
            "json",
        ],
        cwd=root / ".runtime" / "opentelemetry-demo",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    config = json.loads(result.stdout)
    assert config["services"]["flagd"]["networks"]["default"]["aliases"] == ["badhost"]
    demo_images = {
        service: settings["image"]
        for service, settings in config["services"].items()
        if settings.get("image", "").startswith("ghcr.io/open-telemetry/demo:")
    }
    assert demo_images
    assert all(
        image.startswith("ghcr.io/open-telemetry/demo:2.2.0-") for image in demo_images.values()
    ), demo_images


@pytest.mark.integration
def test_spanmetrics_uses_stable_resource_identity() -> None:
    root = Path(__file__).parents[2]
    extras = root / "infra" / "otel-demo" / "otelcol-config-extras.yml"
    config = yaml.safe_load(extras.read_text(encoding="utf-8"))

    assert config["connectors"]["spanmetrics"]["resource_metrics_key_attributes"] == [
        "service.name",
        "telemetry.sdk.language",
        "telemetry.sdk.name",
    ]
    assert config["connectors"]["spanmetrics"]["metrics_flush_interval"] == "15s"

    docker = shutil.which("docker")
    assert docker is not None
    result = subprocess.run(  # noqa: S603 - fixed compose arguments and resolved executable
        [
            docker,
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            str(root / "infra" / "otel-demo" / "docker-compose.incidentpilot.yml"),
            "config",
            "--format",
            "json",
        ],
        cwd=root / ".runtime" / "opentelemetry-demo",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    compose = json.loads(result.stdout)
    extras_mount = next(
        volume
        for volume in compose["services"]["otel-collector"]["volumes"]
        if volume["target"] == "/etc/otelcol-config-extras.yml"
    )
    assert Path(extras_mount["source"]).resolve() == extras.resolve()
