from incidentpilot.bootstrap import _timeline_payload  # pyright: ignore[reportPrivateUsage]


def test_read_only_graph_state_is_exposed_as_structured_public_report() -> None:
    payload = _timeline_payload(
        {
            "incident_id": "inc-1",
            "status": "RESOLVED_READ_ONLY",
            "diagnosis": {
                "symptom_service": "checkout",
                "root_cause_service": "payment",
                "dependency_service": "payment",
                "root_cause_category": "dependency_failure",
                "root_cause_summary": "Payment requests are failing.",
                "confidence": 0.92,
                "evidence_ids": ["ev-metric", "ev-trace"],
                "alternatives": [],
                "customer_impact": "Checkout is unavailable.",
                "diagnosis_limits": [],
            },
            "hypotheses": [],
            "reports": [],
            "evidence_ids": ["ev-metric", "ev-trace"],
            "tool_call_ids": ["tool-metric", "tool-trace"],
        }
    )

    assert payload["report"]["incident_id"] == "inc-1"
    assert payload["report"]["diagnosis"]["root_cause_service"] == "payment"
    assert payload["report"]["evidence_ids"] == ["ev-metric", "ev-trace"]
