# ADR 0002: Read-only telemetry MCP boundary

Date: 2026-08-02  
Status: accepted

## Decision

Expose telemetry through a separate **read-only telemetry** MCP service with a
small allowlist of typed tools. Metrics, logs, and traces investigators receive
only their own tool subset and bounded context. The service uses a dedicated
least-privilege role; it has no shell, SQL console, Docker socket, write action,
or arbitrary URL capability.

## Rationale

Telemetry payloads are untrusted data and may contain prompt injection. A
bounded service creates an enforceable policy boundary, keeps query limits and
redaction near the backend client, and allows tool calls and Evidence digests to
be audited independently of the model.

## Consequences

Adding a telemetry capability requires a typed contract and coverage for normal,
bad-parameter, timeout, permission, and truncation paths. Convenience access to
arbitrary observability queries is deliberately rejected.
