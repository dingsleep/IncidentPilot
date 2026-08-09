---
id: email.memory-leak
version: 1.0.0
services: [email]
symptoms: [email out of memory, email heap growth, mail worker memory leak]
preconditions: [email memory rises across multiple samples]
risk: medium
last_verified_at: 2026-07-16
sources: [https://opentelemetry.io/docs/demo/]
---
# Email memory leak

## Diagnosis

Confirm memory growth belongs to the email service and correlate it with queue depth, delivery
latency, restarts, and email worker logs.

## Procedure

### Step 1: verify email worker memory growth

- Applies when: email memory rises across multiple samples with delivery degradation.
- Do not use when: recommendation or another service is the only process under memory pressure.
- Action: inspect email memory, queue, error logs, and delivery traces in the same interval.
- Validate: email-specific evidence supports sustained growth and degraded delivery.
- Rollback: no state is changed; discard the hypothesis if the service attribution is wrong.

### Step 2: propose email worker recovery

- Applies when: the email leak is confirmed and an owner-approved recovery action exists.
- Do not use when: pending messages cannot be preserved or compensation is undefined.
- Action: submit a bounded email worker recovery proposal without executing it.
- Validate: delivery resumes and queued messages drain without duplication.
- Rollback: restore the prior worker version or replica configuration through an approved action.
