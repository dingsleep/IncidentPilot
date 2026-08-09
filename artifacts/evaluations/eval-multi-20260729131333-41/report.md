# Evaluation eval-multi-20260729131333-41

Suite: `train-v2-score-v4`  
Candidate: `p1-8dbad800e402:qwen3.7-flash:json_output:q-2f310decbea1:t-telemetry-v8:s-v6-tax2`  
Mode: `multi`

| Score | Root cause | Evidence | Hard failures | Cost (micro-USD) | Duration (ms) |
|---:|---:|---:|---:|---:|---:|
| 0.912 | 1.000 | 1.000 | 0 | 2590 | 74382 |

## Failed Episodes

- [llm-rate-limit-001](report.json#llm-rate-limit-001) — 0.800; score below 1.0
- [payment-failure-001](report.json#payment-failure-001) — 0.850; score below 1.0
