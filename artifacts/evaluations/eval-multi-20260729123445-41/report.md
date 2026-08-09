# Evaluation eval-multi-20260729123445-41

Suite: `train-v2-score-v4`  
Candidate: `p1-70e19ceecce6:qwen3.7-flash:json_output:q-2f310decbea1:t-telemetry-v8:s-v5-tax1`  
Mode: `multi`

| Score | Root cause | Evidence | Hard failures | Cost (micro-USD) | Duration (ms) |
|---:|---:|---:|---:|---:|---:|
| 0.738 | 0.500 | 1.000 | 0 | 2190 | 67584 |

## Failed Episodes

- [ad-failure-001](report.json#ad-failure-001) — 0.850; score below 1.0
- [llm-rate-limit-001](report.json#llm-rate-limit-001) — 0.550; score below 1.0
- [product-catalog-failure-001](report.json#product-catalog-failure-001) — 0.550; score below 1.0
