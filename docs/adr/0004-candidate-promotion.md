# ADR 0004: Offline candidate promotion only

Date: 2026-08-02  
Status: accepted

## Decision

Evolution can generate immutable candidates and compare them offline, but no
candidate can self-activate. A deterministic **promotion** gate evaluates
quality, cost, root cause, safety, historical safety, and execution metadata;
it may recommend staging or reject. Active changes require an explicit human
approval and audit event.

## Rationale

Online self-modification would let noisy telemetry, benchmark overfitting, or a
model error change prompts, permissions, or behavior without review. A rejected
candidate is useful evidence and must remain visible rather than being silently
replaced.

## Consequences

Only a frozen staging candidate may enter a user-authorized private holdout.
The observed holdout closes that suite version to further tuning. The current
public candidate was rejected on validation regression and the active prompt was
not changed.
