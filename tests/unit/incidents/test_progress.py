from __future__ import annotations

import pytest

from incidentpilot.incidents.progress import progress_payload


def test_progress_payload_exposes_only_structured_runtime_facts() -> None:
    payload = progress_payload(
        stage="investigation",
        status="running",
        message="正在查询支付服务的错误日志",
        agent="logs_investigator",
        details={
            "services": ["checkout", "payment"],
            "evidence_count": 2,
            "duration_ms": 241,
        },
    )

    assert payload == {
        "stage": "investigation",
        "status": "running",
        "message": "正在查询支付服务的错误日志",
        "agent": "logs_investigator",
        "details": {
            "services": ["checkout", "payment"],
            "evidence_count": 2,
            "duration_ms": 241,
        },
    }


@pytest.mark.parametrize("field", ["chain_of_thought", "reasoning", "prompt", "raw_response"])
def test_progress_payload_rejects_private_model_material(field: str) -> None:
    with pytest.raises(ValueError, match="private model material"):
        progress_payload(
            stage="investigation",
            status="running",
            message="调查中",
            details={field: "must never be persisted"},
        )


def test_progress_payload_rejects_unknown_stage_and_status() -> None:
    with pytest.raises(ValueError, match="stage"):
        progress_payload(stage="thinking", status="running", message="调查中")
    with pytest.raises(ValueError, match="status"):
        progress_payload(stage="triage", status="spinning", message="调查中")
