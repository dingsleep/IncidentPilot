from __future__ import annotations

from pathlib import Path

import pytest

from incidentpilot.knowledge.loader import load_runbook

VALID_RUNBOOK = """\
---
id: payment.dependency-unreachable
version: 1.0.0
services: [payment]
symptoms: [dependency unavailable, connection refused]
preconditions: [payment errors are elevated]
risk: medium
last_verified_at: 2026-07-16
sources: [https://opentelemetry.io/docs/demo/]
---
# Payment dependency unreachable

## Diagnosis

Check dependency errors and traces.

## Procedure

### Step 1: verify dependency reachability

- Applies when: payment errors contain connection failures.
- Do not use when: the dependency is intentionally disabled.
- Action: query read-only dependency telemetry.
- Validate: error rate and failed spans identify the same dependency.
- Rollback: no state is changed; discard the hypothesis.
"""


def test_loader_validates_frontmatter_and_preserves_versioned_sections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runbook.md"
    path.write_text(VALID_RUNBOOK, encoding="utf-8")

    runbook = load_runbook(path)

    assert runbook.id == "payment.dependency-unreachable"
    assert runbook.version == "1.0.0"
    assert runbook.digest
    assert [section.section_id for section in runbook.sections] == [
        "diagnosis",
        "procedure",
        "procedure/step-1-verify-dependency-reachability",
    ]
    assert runbook.sections[-1].parent_title == "Procedure"
    assert runbook.sections[-1].runbook_digest == runbook.digest
    assert runbook.sections[-1].checksum


def test_loader_rejects_incomplete_operational_step(tmp_path: Path) -> None:
    path = tmp_path / "runbook.md"
    path.write_text(
        VALID_RUNBOOK.replace(
            "- Rollback: no state is changed; discard the hypothesis.\n",
            "",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Rollback"):
        load_runbook(path)
