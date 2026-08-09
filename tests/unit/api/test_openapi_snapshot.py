import json
from pathlib import Path

from incidentpilot.api.main import create_app


def test_web_openapi_snapshot_matches_application_schema() -> None:
    snapshot = Path(__file__).parents[3] / "web" / "openapi.json"

    assert json.loads(snapshot.read_text(encoding="utf-8")) == create_app().openapi()


def test_workbench_responses_are_typed_in_openapi() -> None:
    schema = create_app().openapi()

    assert "EvidenceView" in schema["components"]["schemas"]
    assert "TimelineEventView" in schema["components"]["schemas"]
    paths = schema["paths"]
    assert "TimelineEventView" in json.dumps(
        paths["/api/v1/incidents/{incident_id}/timeline"]["get"]["responses"]["200"]
    )
    assert "EvidenceView" in json.dumps(
        paths["/api/v1/incidents/{incident_id}/evidence"]["get"]["responses"]["200"]
    )
