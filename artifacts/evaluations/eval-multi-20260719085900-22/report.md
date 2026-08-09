# Evaluation eval-multi-20260719085900-22

Suite: `validation-v1`  
Candidate: `prompts-v1:deepseek-v4-flash`  
Mode: `multi`

| Score | Root cause | Evidence | Hard failures | Cost (micro-USD) | Duration (ms) |
|---:|---:|---:|---:|---:|---:|
| 0.362 | 0.000 | 0.750 | 0 | 9314 | 105157 |

## Failed Episodes

- [cart-failure-001](report.json#cart-failure-001) — 0.400; score below 1.0
- [no-fault-control-001](report.json#no-fault-control-001) — 0.250; score below 1.0
- [payment-unreachable-001](report.json#payment-unreachable-001) — 0.400; score below 1.0
- [recommendation-cache-leak-001](report.json#recommendation-cache-leak-001) — 0.400; score below 1.0
