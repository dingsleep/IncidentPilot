from __future__ import annotations

from incidentpilot.remediation.idempotency import InMemoryExecutionIdempotencyStore


def test_idempotency_store_returns_the_original_execution_for_repeated_key() -> None:
    store = InMemoryExecutionIdempotencyStore()

    first = store.reserve(
        proposal_id="proposal-1", idempotency_key="restart-checkout-inc-1", execution_id="exec-1"
    )
    completed = store.complete("exec-1", status="succeeded", result={"target": "checkout"})
    repeated = store.reserve(
        proposal_id="proposal-1", idempotency_key="restart-checkout-inc-1", execution_id="exec-2"
    )

    assert not first.replayed
    assert completed.status == "succeeded"
    assert repeated.replayed
    assert repeated.execution_id == "exec-1"
    assert repeated.result == {"target": "checkout"}
