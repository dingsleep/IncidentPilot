# Evaluation eval-baseline-20260730023140-41

Suite: `validation-v2-score-v5`  
Candidate: `p1-9030b15a9d6b:qwen3.7-flash:json_output:q-2f310decbea1:t-telemetry-v9:s-v8-tax4`  
Mode: `baseline`

| Score | Root cause | Evidence | Hard failures | Cost (micro-USD) | Duration (ms) |
|---:|---:|---:|---:|---:|---:|
| 0.679 | 0.500 | 0.750 | 0 | 1771 | 47089 |

## Failed Episodes

- [cart-failure-001](report.json#cart-failure-001) — 0.850; score below 1.0
- [no-fault-control-001](report.json#no-fault-control-001) — 0.400; score below 1.0
- [payment-unreachable-001](report.json#payment-unreachable-001) — 0.817; score below 1.0
- [recommendation-cache-leak-001](report.json#recommendation-cache-leak-001) — 0.650; score below 1.0
