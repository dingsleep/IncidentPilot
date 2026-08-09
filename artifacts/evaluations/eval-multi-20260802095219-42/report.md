# Evaluation eval-multi-20260802095219-42

Suite: `validation-v2-score-v5`  
Candidate: `p1-9030b15a9d6b:qwen3.7-flash:json_output:q-2f310decbea1:t-telemetry-v9:s-v8-tax4`  
Mode: `multi`

| Score | Root cause | Evidence | Hard failures | Cost (micro-USD) | Duration (ms) |
|---:|---:|---:|---:|---:|---:|
| 0.863 | 0.750 | 1.000 | 0 | 2952 | 304809 |

## Failed Episodes

- [payment-unreachable-001](report.json#payment-unreachable-001) — 0.850; score below 1.0
- [recommendation-cache-leak-001](report.json#recommendation-cache-leak-001) — 0.600; score below 1.0
