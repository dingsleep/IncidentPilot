from __future__ import annotations

import time
import uuid
from typing import Any, cast

import httpx
import pytest

from incidentpilot.evaluation.isolation import FlagdScenarioController

FRONTEND = "http://127.0.0.1:8080"
PROMETHEUS = "http://127.0.0.1:9090"
ATTEMPTS = 6


def _checkout(client: httpx.Client) -> httpx.Response:
    user_id = str(uuid.uuid4())
    product_id = "0PUK6V6EV0"
    product = client.get(f"{FRONTEND}/api/products/{product_id}")
    product.raise_for_status()
    cart = client.post(
        f"{FRONTEND}/api/cart",
        json={"item": {"productId": product_id, "quantity": 1}, "userId": user_id},
    )
    cart.raise_for_status()
    return client.post(
        f"{FRONTEND}/api/checkout",
        json={
            "userId": user_id,
            "email": "incidentpilot@example.com",
            "address": {
                "streetAddress": "1600 Amphitheatre Parkway",
                "zipCode": "94043",
                "city": "Mountain View",
                "state": "CA",
                "country": "United States",
            },
            "userCurrency": "USD",
            "creditCard": {
                "creditCardNumber": "4432-8015-6152-0454",
                "creditCardExpirationMonth": 1,
                "creditCardExpirationYear": 2039,
                "creditCardCvv": 672,
            },
        },
    )


def _counters(client: httpx.Client, status: str) -> dict[str, float]:
    query = (
        "sum by (service_name) (traces_span_metrics_calls_total{"
        'service_name=~"checkout|payment",'
        'span_name=~"oteldemo.CheckoutService/PlaceOrder|'
        'oteldemo.PaymentService/Charge|grpc.oteldemo.PaymentService/Charge",'
        f'status_code="{status}"'
        "})"
    )
    response = client.get(f"{PROMETHEUS}/api/v1/query", params={"query": query})
    response.raise_for_status()
    payload = cast(dict[str, Any], response.json())
    data = cast(dict[str, Any], payload["data"])
    result = cast(list[dict[str, Any]], data["result"])
    return {
        cast(dict[str, str], item["metric"])["service_name"]: float(
            cast(list[Any], item["value"])[1]
        )
        for item in result
    }


def _wait_for_delta(
    client: httpx.Client,
    *,
    status: str,
    baseline: dict[str, float],
    expected: dict[str, int],
    timeout: float = 90,
) -> dict[str, float]:
    deadline = time.monotonic() + timeout
    current: dict[str, float] = {}
    while time.monotonic() < deadline:
        current = _counters(client, status)
        delta = {
            service: current.get(service, 0) - baseline.get(service, 0) for service in expected
        }
        if all(delta[service] >= count for service, count in expected.items()):
            return delta
        time.sleep(2)
    pytest.fail(f"timed out waiting for {status} metric delta; current={current}")


@pytest.mark.integration
def test_payment_failure_emits_errors_then_restores_and_error_rate_falls() -> None:
    with httpx.Client(timeout=15, trust_env=False) as client:
        controller = FlagdScenarioController(client=client, poll_interval=0.5, timeout=15)
        original = controller.snapshot()
        fault_error_start = _counters(client, "STATUS_CODE_ERROR")

        with controller.activate("paymentFailure", "100%"):
            time.sleep(3)
            fault_responses = [_checkout(client) for _ in range(ATTEMPTS)]
            assert all(response.status_code >= 500 for response in fault_responses)
            _wait_for_delta(
                client,
                status="STATUS_CODE_ERROR",
                baseline=fault_error_start,
                expected={"checkout": ATTEMPTS * 2, "payment": ATTEMPTS},
            )

        assert controller.snapshot().digest == original.digest
        time.sleep(3)
        recovery_responses = [_checkout(client) for _ in range(ATTEMPTS)]
        assert all(response.status_code == 200 for response in recovery_responses)

    fault_error_rate = sum(response.status_code >= 500 for response in fault_responses) / ATTEMPTS
    recovery_error_rate = (
        sum(response.status_code >= 500 for response in recovery_responses) / ATTEMPTS
    )
    assert fault_error_rate == 1
    assert recovery_error_rate < fault_error_rate
