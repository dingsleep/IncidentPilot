from __future__ import annotations

from incidentpilot.observability.redaction import redact_data


def test_redaction_covers_headers_keys_email_and_payment_patterns_without_mutation() -> None:
    payload = {
        "Authorization": "Bearer top-secret",
        "headers": {"Cookie": "session=abc", "X-API-Key": "key-123"},
        "config": {"client_secret": "secret-value", "safe": "visible"},
        "message": "Contact sre@example.com using card 4111-1111-1111-1111",
        "nested": [{"password": "hunter2", "email": "owner@example.com"}],
    }

    redacted = redact_data(payload)

    assert payload["Authorization"] == "Bearer top-secret"
    assert redacted == {
        "Authorization": "[REDACTED]",
        "config": {"client_secret": "[REDACTED]", "safe": "visible"},
        "headers": {"Cookie": "[REDACTED]", "X-API-Key": "[REDACTED]"},
        "message": "Contact [REDACTED_EMAIL] using card [REDACTED_PAYMENT]",
        "nested": [{"email": "[REDACTED]", "password": "[REDACTED]"}],
    }


def test_redaction_does_not_corrupt_numeric_runs_inside_internal_ids() -> None:
    proposal_id = "proposal_fab4968571be4277634395021e21d06"

    assert redact_data({"proposal_id": proposal_id}) == {"proposal_id": proposal_id}
