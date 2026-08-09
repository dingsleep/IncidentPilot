# ADR 0004: Controlled evolution and promotion

Date: 2026-08-02  
Status: accepted

## Decision

Evolution produces immutable candidate artifacts and evaluates them offline. A
deterministic **promotion** gate can reject or recommend staging, but cannot
write an active prompt. Any activation requires a human decision and an audit
record.

## Rationale

Online self-modification would allow noisy telemetry, overfitting, or model
errors to alter behavior or permissions without review. Retaining negative
results is necessary to prevent repeated failed suggestions.

## Consequences

Only a frozen staging candidate may enter a user-authorized private holdout.
Seeing a holdout result closes that suite version to further tuning. The current
public candidate was rejected on validation regression; the active prompt was
not changed.
