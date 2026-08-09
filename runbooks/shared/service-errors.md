---
id: shared.service-errors
version: 1.0.0
services: [checkout, frontend, payment, recommendation, email, ad]
symptoms: [service errors, elevated error ratio, internal error, failed requests]
preconditions: [one or more services show elevated errors]
risk: low
last_verified_at: 2026-07-16
sources: [https://opentelemetry.io/docs/demo/]
---
# Shared service error triage

## Diagnosis

Use this fallback only when no service-specific runbook has stronger evidence. Establish the
first failing service and distinguish upstream propagation from the originating failure.

## Procedure

### Step 1: localize the first failing service

- Applies when: multiple services report errors and the origin is not yet known.
- Do not use when: a service-specific diagnosis already has two corroborating signal kinds.
- Action: compare error metrics, earliest failing spans, log timestamps, and recent public changes.
- Validate: the proposed origin precedes downstream errors and has direct local evidence.
- Rollback: no state is changed; discard the origin hypothesis if ordering or evidence conflicts.

### Step 2: select a service-specific runbook

- Applies when: the originating service and symptom family are supported by evidence.
- Do not use when: only a generic error string is available.
- Action: retrieve the matching versioned service runbook and cite its section checksum.
- Validate: the selected runbook service and preconditions match the incident evidence.
- Rollback: return to generic triage if the specific runbook preconditions fail.
