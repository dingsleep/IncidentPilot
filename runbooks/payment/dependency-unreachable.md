---
id: payment.dependency-unreachable
version: 1.0.0
services: [payment]
symptoms: [dependency unavailable, connection refused, upstream timeout, payment failure]
preconditions: [payment error ratio is elevated, failures correlate with one dependency]
risk: medium
last_verified_at: 2026-07-16
sources: [https://opentelemetry.io/docs/demo/]
---
# Payment dependency unreachable

## Diagnosis

Confirm that payment failures, dependency spans, and connection-related logs identify the
same upstream. A generic checkout error alone is insufficient.

## Procedure

### Step 1: confirm the unreachable dependency

- Applies when: payment errors contain connection refused, unavailable, or timeout signals.
- Do not use when: payment traffic is healthy or the dependency is intentionally disabled.
- Action: query payment error metrics, matching logs, and dependency traces for the same window.
- Validate: at least two telemetry kinds identify the same upstream and failure interval.
- Rollback: no state is changed; discard the hypothesis if the signals do not correlate.

### Step 2: propose dependency recovery

- Applies when: the dependency failure is confirmed and an approved recovery action exists.
- Do not use when: approval, ownership, idempotency, or rollback information is missing.
- Action: create a proposal for the deterministic remediation service; do not execute it here.
- Validate: the proposal names the dependency, expected health signal, and approval requirement.
- Rollback: reject or expire the proposal before execution; use its declared compensation after execution.
