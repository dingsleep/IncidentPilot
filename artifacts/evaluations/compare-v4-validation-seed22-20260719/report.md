# Evaluation compare-v4-validation-seed22-20260719

Suite: `validation-v1`  
Candidate: `prompts-v1:deepseek-v4-flash`

## Baseline vs multi

| Metric | Baseline | Multi | Delta |
|---|---:|---:|---:|
| Weighted score | 0.542 | 0.362 | -0.179 |
| Root-cause accuracy | 0.500 | 0.000 | -0.500 |
| Evidence fidelity | 0.750 | 0.750 | +0.000 |
| Cost (micro-USD) | 3747 | 9314 | +5567 |
| Duration (ms) | 29730 | 105157 | +75427 |
| Tool calls | 12 | 12 | +0 |

## Failed Episodes

- [baseline/cart-failure-001](../eval-baseline-20260719090442-22/report.json#cart-failure-001) — 0.400; score below 1.0
- [baseline/no-fault-control-001](../eval-baseline-20260719090442-22/report.json#no-fault-control-001) — 0.850; score below 1.0
- [baseline/payment-unreachable-001](../eval-baseline-20260719090442-22/report.json#payment-unreachable-001) — 0.517; score below 1.0
- [baseline/recommendation-cache-leak-001](../eval-baseline-20260719090442-22/report.json#recommendation-cache-leak-001) — 0.400; score below 1.0
- [multi/cart-failure-001](../eval-multi-20260719085900-22/report.json#cart-failure-001) — 0.400; score below 1.0
- [multi/no-fault-control-001](../eval-multi-20260719085900-22/report.json#no-fault-control-001) — 0.250; score below 1.0
- [multi/payment-unreachable-001](../eval-multi-20260719085900-22/report.json#payment-unreachable-001) — 0.400; score below 1.0
- [multi/recommendation-cache-leak-001](../eval-multi-20260719085900-22/report.json#recommendation-cache-leak-001) — 0.400; score below 1.0
