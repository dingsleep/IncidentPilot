# Evaluation compare-v4-payment-unreachable-seed13-20260719

Suite: `validation-v1`  
Candidate: `prompts-v1:deepseek-v4-flash`

## Baseline vs multi

| Metric | Baseline | Multi | Delta |
|---|---:|---:|---:|
| Weighted score | 0.317 | 0.400 | +0.083 |
| Root-cause accuracy | 0.000 | 0.000 | +0.000 |
| Evidence fidelity | 0.000 | 1.000 | +1.000 |
| Cost (micro-USD) | 2268 | 2908 | +640 |
| Duration (ms) | 23225 | 34991 | +11766 |
| Tool calls | 3 | 3 | +0 |

## Failed Episodes

- [baseline/payment-unreachable-001](../eval-baseline-20260719083553-13/report.json) — 0.317; score below 1.0
- [multi/payment-unreachable-001](../eval-multi-20260719083721-13/report.json) — 0.400; score below 1.0
