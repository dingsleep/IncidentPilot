# ADR 0002: Bounded multi-agent graph

Date: 2026-08-02  
Status: accepted

## Decision

IncidentPilot uses a **bounded** state graph with specialist, stateless
investigators rather than a free-form multi-agent conversation. State is typed,
JSON-serializable, checkpointed, and routed through explicit fan-out/fan-in
nodes. Metrics, logs, and traces receive only their respective read-only tools
and bounded Evidence context.

## Rationale

Free-form agent chat makes tool authority, state ownership, termination, cost,
and auditability ambiguous. The bounded graph makes budgets and stop conditions
enforceable and lets deterministic code validate the resulting candidate.

## Consequences

New investigator behavior must declare its state input/output, tool allowlist,
budget, and failure behavior. More agents are not treated as an automatic
quality improvement.
