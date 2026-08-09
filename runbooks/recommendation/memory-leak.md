---
id: recommendation.memory-leak
version: 1.0.0
services: [recommendation]
symptoms: [out of memory, heap growth, memory leak, increasing rss]
preconditions: [recommendation memory rises across multiple samples]
risk: high
last_verified_at: 2026-07-16
sources: [https://opentelemetry.io/docs/demo/]
---
# Recommendation memory leak

## Diagnosis

Distinguish sustained heap or RSS growth from a short traffic spike. Check for OOM events,
restarts, latency growth, and allocation-related logs.

## Procedure

### Step 1: verify sustained recommendation memory growth

- Applies when: recommendation memory grows across multiple samples and does not return to baseline.
- Do not use when: only email or another service shows memory pressure.
- Action: compare recommendation memory, request rate, restart count, logs, and traces.
- Validate: memory growth persists independently of a transient request-rate increase.
- Rollback: no state is changed; discard the leak hypothesis when memory tracks traffic normally.

### Step 2: propose a bounded restart

- Applies when: the leak is confirmed and a restart is an approved temporary mitigation.
- Do not use when: no healthy replica, rollback path, approval, or post-restart verification exists.
- Action: submit a single-service restart proposal through the deterministic action boundary.
- Validate: memory and latency return to baseline without shifting errors downstream.
- Rollback: execute the approved deployment rollback or restore the prior replica set if health worsens.
