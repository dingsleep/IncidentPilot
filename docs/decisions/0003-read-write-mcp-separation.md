# ADR 0003: Read/write MCP separation

Date: 2026-08-02  
Status: accepted

## Decision

Telemetry is exposed through a dedicated read-only MCP service with typed,
bounded tools and least-privilege credentials. Write actions use a separate MCP
boundary and credentials. A model proposal is never an execution right: a
**human approval** produces a signed, scope-bound grant, then deterministic
code verifies scope, proposal digest, tenant, nonce, idempotency, and outcome.

## Rationale

Separating data access from authority prevents a prompt or tool-selection error
from gaining execution capability. It also makes audit, replay protection, and
rollback paths independently testable.

## Consequences

The core Compose profile has no Docker socket and no Action MCP. The optional
actions profile requires explicit approval-verifying and mapping-encryption
keys, has no Docker Socket mount, and exposes only approved mapping-backed
flagd rollback. It is not presented as a production write service.
