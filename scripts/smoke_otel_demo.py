from __future__ import annotations

import sys
import time
from collections.abc import Callable

import httpx


def check_endpoint(client: httpx.Client, name: str, url: str) -> list[str]:
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as error:
        return [f"{name}: {error}"]
    return []


def check_jaeger(client: httpx.Client) -> list[str]:
    try:
        services = set(
            client.get("http://127.0.0.1:16686/jaeger/ui/api/v3/services")
            .raise_for_status()
            .json()["services"]
        )
        traces = (
            client.get(
                "http://127.0.0.1:16686/jaeger/ui/api/traces?service=frontend&limit=1&lookback=1h"
            )
            .raise_for_status()
            .json()["data"]
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        return [f"jaeger: {error}"]

    missing = {"frontend", "checkout", "payment"} - services
    failures = [f"jaeger: missing services {sorted(missing)}"] if missing else []
    return failures + ([] if traces else ["jaeger: no frontend trace from the last hour"])


def main() -> int:
    checks: list[Callable[[httpx.Client], list[str]]] = [
        lambda client: check_endpoint(client, "storefront", "http://127.0.0.1:8080/"),
        lambda client: check_endpoint(client, "grafana", "http://127.0.0.1:3000/api/health"),
        lambda client: check_endpoint(client, "prometheus", "http://127.0.0.1:9090/-/ready"),
        lambda client: check_endpoint(
            client, "opensearch", "http://127.0.0.1:9200/_cluster/health"
        ),
        lambda client: check_endpoint(client, "flagd-ui", "http://127.0.0.1:8080/feature/api/read"),
        check_jaeger,
    ]
    deadline = time.monotonic() + 120
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        while True:
            failures = [failure for check in checks for failure in check(client)]
            if not failures or time.monotonic() >= deadline:
                break
            time.sleep(5)

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("OpenTelemetry Demo smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
