# Failure Taxonomy

M9.2 uses deterministic, public reason-code mapping before any optional semantic clustering. An
unknown code is not silently assigned to a cluster. Each cluster is keyed by both the label and
the affected component, and its representative Episode is the lexicographically smallest public
Episode ID.

| Label | Recognized reason-code examples | Expected metric |
| --- | --- | --- |
| `tool_selection` | `TOOL_SELECTION`, `TOOL_NOT_ALLOWED` | `tool_validity` |
| `invalid_args` | `INVALID_ARGS`, `INVALID_TOOL_ARGUMENTS` | `tool_validity` |
| `duplicate_query` | `DUPLICATE_QUERY`, `DUPLICATE_TOOL_CALL` | `duplicate_call_rate` |
| `missing_signal` | `MISSING_SIGNAL`, `TOOL_RESULT_MISSING` | `signal_coverage` |
| `wrong_synthesis` | `WRONG_SYNTHESIS`, `ROOT_CAUSE_MISMATCH` | `root_cause_accuracy` |
| `unsupported_claim` | `UNSUPPORTED_CLAIM`, evidence-integrity failures | `evidence_fidelity` |
| `policy_rejection` | `POLICY_REJECTION`, `UNAPPROVED_WRITE` | `policy_rejection_rate` |
| `no_recovery` | `NO_RECOVERY`, `RECOVERY_FAILED` | `recovery_success_rate` |
| `environment` | `ENVIRONMENT_CONTAMINATED`, `CLEANUP_FAILED` | `environment_cleanliness` |

The output is a suggestion, not a candidate artifact and never a live edit. Every suggestion
records its source cluster, component, expected metric, and a specific regression risk. Optional
embeddings may later help discover subclusters, but cannot replace these labels or choose a
representative Episode.
