# Evaluation eval-multi-20260802101724-43

Suite: `train-v2-score-v5`  
Candidate: `p1-9030b15a9d6b:qwen3.7-flash:json_output:q-2f310decbea1:t-telemetry-v9:s-v8-tax4`  
Mode: `multi`

| Score | Root cause | Evidence | Hard failures | Cost (micro-USD) | Duration (ms) |
|---:|---:|---:|---:|---:|---:|
| 0.738 | 0.500 | 1.000 | 0 | 3379 | 107378 |

## Failed Episodes

- [llm-rate-limit-001](report.json#llm-rate-limit-001) — 0.850; score below 1.0
- [payment-failure-001](report.json#payment-failure-001) — 0.550; score below 1.0
- [product-catalog-failure-001](report.json#product-catalog-failure-001) — 0.550; score below 1.0
