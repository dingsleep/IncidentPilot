---
id: ad.high-cpu
version: 1.0.0
services: [ad]
symptoms: [high cpu, cpu saturation, throttling, slow ad responses]
preconditions: [ad cpu is elevated across multiple samples]
risk: medium
last_verified_at: 2026-07-16
sources: [https://opentelemetry.io/docs/demo/]
---
# Ad service high CPU

## Diagnosis

Correlate CPU saturation with request rate, throttling, latency, hot endpoints, and error logs.
Do not infer a CPU fault from latency alone.

## Procedure

### Step 1: confirm ad CPU saturation

- Applies when: ad CPU and latency are elevated for the same sustained interval.
- Do not use when: CPU is normal or latency originates in an upstream dependency.
- Action: compare ad CPU, request rate, latency, logs, and traces by endpoint.
- Validate: CPU saturation and degraded ad spans share the same interval and service.
- Rollback: no state is changed; discard the hypothesis when dependency latency explains the symptom.

### Step 2: propose bounded capacity recovery

- Applies when: saturation is confirmed and a predefined scaling or rollback action is available.
- Do not use when: approval, capacity limits, validation, or compensation is missing.
- Action: submit the smallest predefined capacity or deployment rollback proposal.
- Validate: CPU and latency fall while error rate and downstream load remain healthy.
- Rollback: restore the previous replica count or deployment version through the approved compensation.
