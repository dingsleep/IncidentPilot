# Evaluation eval-baseline-20260719090442-22

Suite: `validation-v1`  
Candidate: `prompts-v1:deepseek-v4-flash`  
Mode: `baseline`

| Score | Root cause | Evidence | Hard failures | Cost (micro-USD) | Duration (ms) |
|---:|---:|---:|---:|---:|---:|
| 0.542 | 0.500 | 0.750 | 0 | 3747 | 29730 |

## Failed Episodes

- [cart-failure-001](report.json#cart-failure-001) — 0.400; score below 1.0
- [no-fault-control-001](report.json#no-fault-control-001) — 0.850; score below 1.0
- [payment-unreachable-001](report.json#payment-unreachable-001) — 0.517; score below 1.0
- [recommendation-cache-leak-001](report.json#recommendation-cache-leak-001) — 0.400; score below 1.0
