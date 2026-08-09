# ADR 0003: Human approval and deterministic action authorization

Date: 2026-08-02  
Status: accepted

## Decision

An action proposal is not an execution right. A **human approval** creates a
short-lived, signed, scope-bound grant. Deterministic code re-reads the stored
incident and proposal, validates tenant/status/digest/actor/scope, atomically
consumes the nonce, enforces idempotency, records the audit result, and verifies
the post-action SLO. The model never receives a direct write tool.

## Rationale

This separates diagnosis uncertainty from authority and makes replay,
cross-tenant use, stale proposals, and duplicate executions testable failures.
It also preserves an actionable `NEEDS_HUMAN` outcome when execution or
verification fails.

## Consequences

Action MCP is separate from the core profile and remains disabled by default.
Its optional long-running Compose service requires an Approval verifying key
and a private-mapping encryption key. It reads a mapping only for the approved
change ID, supports rollback only, and has no Docker Socket mount; restart
remains disabled until a separately reviewed fixed container mapping exists.
