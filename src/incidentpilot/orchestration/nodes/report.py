from __future__ import annotations

from typing import Any

from incidentpilot.domain.diagnosis import Diagnosis, RootCauseHypothesis
from incidentpilot.domain.enums import IncidentStatus
from incidentpilot.orchestration.state import ReportArtifact, WaveReport


class ReportNode:
    """Render only typed graph values; this node deliberately has no model dependency."""

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        payload = _json_payload(state)
        lines = [
            f"# Incident {payload['incident_id']}",
        ]
        if status := payload.get("status"):
            lines.extend(["", f"Status: {status}"])
        if reason := payload.get("terminal_reason"):
            lines.extend(["", f"Terminal reason: {reason}"])
        if diagnosis := payload.get("diagnosis"):
            lines.extend(
                [
                    "",
                    "## Diagnosis",
                    "",
                    f"Root cause service: {diagnosis['root_cause_service']}",
                    f"Summary: {diagnosis['root_cause_summary']}",
                    f"Confidence: {diagnosis['confidence']}",
                ]
            )
        if evidence_ids := payload.get("evidence_ids"):
            lines.extend(["", "## Evidence", "", *[f"- {item}" for item in evidence_ids]])
        if hypotheses := payload.get("hypotheses"):
            lines.extend(["", "## Hypotheses", ""])
            for hypothesis in hypotheses:
                lines.append(
                    f"- {hypothesis['id']}: {hypothesis['failure_mode']} "
                    f"(confidence={hypothesis['confidence']})"
                )
        return {
            "report": ReportArtifact(markdown="\n".join(lines), json_data=payload).model_dump(
                mode="json"
            ),
        }


def _json_payload(state: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"incident_id": state["incident_id"]}
    if status := state.get("status"):
        payload["status"] = IncidentStatus(status).value
    for key in ("evidence_ids", "tool_call_ids", "terminal_reason"):
        if key in state and state[key] is not None:
            payload[key] = state[key]
    if hypotheses := state.get("hypotheses"):
        payload["hypotheses"] = [
            _model_json(RootCauseHypothesis.model_validate(hypothesis)) for hypothesis in hypotheses
        ]
    if diagnosis := state.get("diagnosis"):
        payload["diagnosis"] = _model_json(Diagnosis.model_validate(diagnosis))
    if reports := state.get("reports"):
        payload["reports"] = [_model_json(WaveReport.model_validate(report)) for report in reports]
    return payload


def _model_json(value: Diagnosis | RootCauseHypothesis | WaveReport) -> dict[str, Any]:
    return value.model_dump(mode="json")
